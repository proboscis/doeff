//! Scheduler types for cooperative multitasking.
//!
//! The scheduler is a ASTStreamFactory that manages tasks, promises,
//! and cooperative scheduling via Transfer-only semantics.

use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock, Weak};

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};

use crate::ast_stream::{ASTStream, ASTStreamStep, StreamLocation};
use crate::capture::{SpawnSite, TraceHop};
use crate::continuation::Continuation;
use crate::do_ctrl::CallArg;
use crate::doeff_generator::DoeffGeneratorFn;
#[cfg(test)]
use crate::effect::Effect;
use crate::effect::{
    dispatch_from_shared, dispatch_into_python, dispatch_ref_as_python,
    make_execution_context_object, DispatchEffect, PyAcquireSemaphore, PyCancelEffect,
    PyCompletePromise, PyCreateExternalPromise, PyCreatePromise, PyCreateSemaphore, PyFailPromise,
    PyGather, PyGetExecutionContext, PyRace, PyReleaseSemaphore, PySpawn, PyTaskCompleted,
};
use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::handler::{
    ASTStreamFactory, ASTStreamProgram, ASTStreamProgramRef, Handler, HandlerInvoke, PythonHandler,
    RustProgramInvocation,
};
use crate::ids::{ContId, DispatchId, PromiseId, TaskId};
use crate::py_shared::PyShared;
use crate::pyvm::{PyResultErr, PyResultOk, PyRustHandlerSentinel};
use crate::step::{DoCtrl, PyException};
use crate::value::Value;
use crate::vm::RustStore;

pub const SCHEDULER_HANDLER_NAME: &str = "SchedulerHandler";

/// Effect variants handled by the scheduler.
#[derive(Debug, Clone)]
pub enum SchedulerEffect {
    Spawn {
        program: Py<PyAny>,
        handlers: Vec<Handler>,
        store_mode: StoreMode,
        creation_site: Option<SpawnSite>,
    },
    Gather {
        items: Vec<Waitable>,
    },
    Race {
        items: Vec<Waitable>,
    },
    CreatePromise,
    CompletePromise {
        promise: PromiseId,
        value: Value,
    },
    FailPromise {
        promise: PromiseId,
        error: PyException,
    },
    CreateExternalPromise,
    CreateSemaphore {
        permits: u64,
    },
    AcquireSemaphore {
        semaphore_id: u64,
    },
    ReleaseSemaphore {
        semaphore_id: u64,
    },
    CancelTask {
        task: TaskId,
    },
    TaskCompleted {
        task: TaskId,
        result: Result<Value, PyException>,
    },
    GetExecutionContext,
}

/// What a task can wait on.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Waitable {
    Task(TaskId),
    Promise(PromiseId),
    ExternalPromise(PromiseId),
}

/// Store isolation mode for spawned tasks.
#[derive(Clone, Copy, Debug)]
pub enum StoreMode {
    /// Child shares the parent's RustStore (reads/writes visible immediately).
    Shared,
    /// Child gets a snapshot of RustStore. Merge policy controls what comes back.
    Isolated { merge: StoreMergePolicy },
}

/// Policy for merging isolated task stores back into parent.
#[derive(Clone, Copy, Debug)]
pub enum StoreMergePolicy {
    /// Merge only logs (append in Gather items order). State/env changes discarded.
    LogsOnly,
}

/// Per-task store state.
#[derive(Debug, Clone)]
pub enum TaskStore {
    Shared,
    Isolated {
        store: RustStore,
        merge: StoreMergePolicy,
    },
}

/// Runtime state of a task.
#[derive(Debug)]
pub enum TaskState {
    Pending {
        cont: Continuation,
        store: TaskStore,
    },
    Done {
        result: Result<Value, PyException>,
        store: TaskStore,
    },
}

/// Runtime state of a promise.
#[derive(Debug)]
pub enum PromiseState {
    Pending,
    Done(Result<Value, PyException>),
}

/// Opaque handle to a spawned task.
#[derive(Clone, Copy, Debug)]
pub struct TaskHandle {
    pub id: TaskId,
}

/// Opaque handle to a promise.
#[derive(Clone, Copy, Debug)]
pub struct PromiseHandle {
    pub id: PromiseId,
}

/// External promise that can be completed from outside the scheduler.
#[derive(Clone, Debug)]
pub struct ExternalPromise {
    pub id: PromiseId,
    pub completion_queue: Option<PyShared>,
}

#[derive(Clone, Debug)]
pub struct TaskMetadata {
    pub parent_task: Option<TaskId>,
    pub spawn_site: Option<SpawnSite>,
    pub spawn_dispatch_id: Option<DispatchId>,
}

#[derive(Clone, Copy, Debug)]
struct SemaphoreWaiter {
    promise: PromiseId,
    waiting_task: Option<TaskId>,
}

#[derive(Clone, Debug)]
struct SemaphoreRuntimeState {
    max_permits: u64,
    available_permits: u64,
    waiters: VecDeque<SemaphoreWaiter>,
    holders: HashMap<Option<TaskId>, u64>,
}

#[derive(Clone, Debug)]
enum WaitMode {
    All,
    Any,
}

#[derive(Clone, Debug)]
struct WaitRequest {
    continuation: Continuation,
    items: Vec<Waitable>,
    mode: WaitMode,
    waiting_task: Option<TaskId>,
    waiting_store: RustStore,
}

fn transfer_to_continuation(k: Continuation, value: Value) -> ASTStreamStep {
    if k.started {
        return ASTStreamStep::Yield(DoCtrl::Transfer {
            continuation: k,
            value,
        });
    }
    ASTStreamStep::Yield(DoCtrl::ResumeContinuation {
        continuation: k,
        value,
    })
}

fn resume_to_continuation(cont: Continuation, result: Value) -> ASTStreamStep {
    if cont.started {
        return ASTStreamStep::Yield(DoCtrl::Resume {
            continuation: cont,
            value: result,
        });
    }
    ASTStreamStep::Yield(DoCtrl::ResumeContinuation {
        continuation: cont,
        value: result,
    })
}

fn throw_to_continuation(k: Continuation, error: PyException) -> ASTStreamStep {
    if k.started {
        return ASTStreamStep::Yield(DoCtrl::TransferThrow {
            continuation: k,
            exception: error,
        });
    }
    ASTStreamStep::Throw(error)
}

fn step_targets_continuation(step: &ASTStreamStep, target: &Continuation) -> bool {
    match step {
        ASTStreamStep::Yield(DoCtrl::Resume { continuation, .. }) => {
            continuation.cont_id == target.cont_id
        }
        ASTStreamStep::Yield(DoCtrl::ResumeContinuation { continuation, .. }) => {
            continuation.cont_id == target.cont_id
        }
        ASTStreamStep::Yield(DoCtrl::Transfer { continuation, .. }) => {
            continuation.cont_id == target.cont_id
        }
        ASTStreamStep::Yield(DoCtrl::TransferThrow { continuation, .. }) => {
            continuation.cont_id == target.cont_id
        }
        _ => false,
    }
}

/// The scheduler's internal state.
pub struct SchedulerState {
    pub ready: VecDeque<TaskId>,
    ready_waiters: VecDeque<WaitRequest>,
    pub tasks: HashMap<TaskId, TaskState>,
    task_metadata: HashMap<TaskId, TaskMetadata>,
    pub promises: HashMap<PromiseId, PromiseState>,
    semaphores: HashMap<u64, SemaphoreRuntimeState>,
    waiters: HashMap<Waitable, Vec<WaitRequest>>,
    external_completion_queue: Option<PyShared>,
    cancel_requested: HashSet<TaskId>,
    pub next_task: u64,
    pub next_promise: u64,
    pub next_semaphore: u64,
    pub current_task: Option<TaskId>,
    execution_context_task_override: Option<TaskId>,
    state_id: u64,
}

static NEXT_SCHEDULER_STATE_ID: AtomicU64 = AtomicU64::new(1);
static SEMAPHORE_DROP_NOTIFICATIONS: OnceLock<Mutex<HashMap<u64, Vec<u64>>>> = OnceLock::new();
static SCHEDULER_STATE_REGISTRY: OnceLock<Mutex<HashMap<u64, Weak<Mutex<SchedulerState>>>>> =
    OnceLock::new();

fn semaphore_drop_notifications() -> &'static Mutex<HashMap<u64, Vec<u64>>> {
    SEMAPHORE_DROP_NOTIFICATIONS.get_or_init(|| Mutex::new(HashMap::new()))
}

fn scheduler_state_registry() -> &'static Mutex<HashMap<u64, Weak<Mutex<SchedulerState>>>> {
    SCHEDULER_STATE_REGISTRY.get_or_init(|| Mutex::new(HashMap::new()))
}

fn register_scheduler_state(state: &Arc<Mutex<SchedulerState>>) {
    let state_id = {
        let guard = state.lock().expect("Scheduler lock poisoned");
        guard.state_id
    };
    let mut registry = scheduler_state_registry()
        .lock()
        .expect("Scheduler lock poisoned");
    registry.insert(state_id, Arc::downgrade(state));
}

fn resolve_scheduler_state(state_id: u64) -> Option<Arc<Mutex<SchedulerState>>> {
    let weak_state = {
        let registry = scheduler_state_registry()
            .lock()
            .expect("Scheduler lock poisoned");
        registry.get(&state_id).cloned()?
    };

    if let Some(state) = weak_state.upgrade() {
        return Some(state);
    }

    let mut registry = scheduler_state_registry()
        .lock()
        .expect("Scheduler lock poisoned");
    registry.remove(&state_id);
    None
}

pub fn notify_semaphore_handle_dropped(state_id: u64, semaphore_id: u64) {
    let mut notifications = semaphore_drop_notifications()
        .lock()
        .expect("Scheduler lock poisoned");
    notifications
        .entry(state_id)
        .or_default()
        .push(semaphore_id);
}

pub fn debug_semaphore_count_for_state(state_id: u64) -> Option<usize> {
    let state = resolve_scheduler_state(state_id)?;
    let mut state = state.lock().expect("Scheduler lock poisoned");
    state.process_semaphore_drop_notifications();
    Some(state.semaphores.len())
}

fn parse_task_completed_result(
    py: Python<'_>,
    result_obj: &Bound<'_, PyAny>,
) -> Result<Result<Value, PyException>, String> {
    if result_obj.is_none() {
        return Err(
            "TaskCompletedEffect/SchedulerTaskCompleted requires task + result".to_string(),
        );
    }

    if result_obj.extract::<PyRef<'_, PyResultOk>>().is_ok() {
        let value_obj = result_obj.getattr("value").map_err(|e| e.to_string())?;
        return Ok(Ok(Value::from_pyobject(&value_obj)));
    }

    if result_obj.extract::<PyRef<'_, PyResultErr>>().is_ok() {
        let error_obj = result_obj.getattr("error").map_err(|e| e.to_string())?;
        return Ok(Err(pyobject_to_exception(py, &error_obj)));
    }

    Err("TaskCompleted.result must be Ok(...) or Err(...)".to_string())
}

fn extract_semaphore_id(obj: &Bound<'_, PyAny>) -> Option<u64> {
    obj.getattr("id").ok()?.extract::<u64>().ok()
}

fn is_internal_source_file(source_file: &str) -> bool {
    let normalized = source_file.replace('\\', "/").to_lowercase();
    normalized == "_effect_wrap" || normalized.contains("/doeff/")
}

fn spawn_site_from_traceback(hops: &[TraceHop]) -> Option<SpawnSite> {
    let mut fallback: Option<SpawnSite> = None;

    // Traceback hops are ordered inner->outer. Spawn attribution should favor
    // the outermost user continuation when Delegate chains are present.
    for hop in hops.iter().rev() {
        let mut hop_fallback: Option<SpawnSite> = None;
        for frame in hop.frames.iter().rev() {
            let candidate = SpawnSite {
                function_name: frame.func_name.clone(),
                source_file: frame.source_file.clone(),
                source_line: frame.source_line,
            };
            if hop_fallback.is_none() {
                hop_fallback = Some(candidate.clone());
            }
            if !is_internal_source_file(&candidate.source_file) {
                return Some(candidate);
            }
        }
        if fallback.is_none() {
            fallback = hop_fallback;
        }
    }

    fallback
}

fn parse_scheduler_python_effect(
    effect: &PyShared,
    creation_site: Option<SpawnSite>,
) -> Result<Option<SchedulerEffect>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);

        if let Ok(spawn) = obj.extract::<PyRef<'_, PySpawn>>() {
            let handlers = extract_handlers_from_python(spawn.handlers.bind(py))?;
            let store_mode = parse_store_mode(spawn.store_mode.bind(py))?;
            return Ok(Some(SchedulerEffect::Spawn {
                program: spawn.program.clone_ref(py),
                handlers,
                store_mode,
                creation_site,
            }));
        }

        if let Ok(gather) = obj.extract::<PyRef<'_, PyGather>>() {
            let mut waitables = Vec::new();
            for item in gather
                .items
                .bind(py)
                .try_iter()
                .map_err(|e| e.to_string())?
            {
                let item = item.map_err(|e| e.to_string())?;
                match extract_waitable(&item) {
                    Some(w) => waitables.push(w),
                    None => return Err("GatherEffect.items must be waitable handles".to_string()),
                }
            }
            return Ok(Some(SchedulerEffect::Gather { items: waitables }));
        }

        if let Ok(race) = obj.extract::<PyRef<'_, PyRace>>() {
            let mut waitables = Vec::new();
            for item in race
                .futures
                .bind(py)
                .try_iter()
                .map_err(|e| e.to_string())?
            {
                let item = item.map_err(|e| e.to_string())?;
                match extract_waitable(&item) {
                    Some(w) => waitables.push(w),
                    None => {
                        return Err("RaceEffect.futures/items must be waitable handles".to_string());
                    }
                }
            }
            return Ok(Some(SchedulerEffect::Race { items: waitables }));
        }

        if obj.extract::<PyRef<'_, PyCreatePromise>>().is_ok() {
            return Ok(Some(SchedulerEffect::CreatePromise));
        }

        if obj.extract::<PyRef<'_, PyCreateExternalPromise>>().is_ok() {
            return Ok(Some(SchedulerEffect::CreateExternalPromise));
        }

        if let Ok(cancel) = obj.extract::<PyRef<'_, PyCancelEffect>>() {
            let task_obj = cancel.task.bind(py);
            let Some(task) = extract_task_id(task_obj) else {
                return Err("PyCancelEffect.task must carry _handle.task_id".to_string());
            };
            return Ok(Some(SchedulerEffect::CancelTask { task }));
        }

        if let Ok(complete) = obj.extract::<PyRef<'_, PyCompletePromise>>() {
            let promise_obj = complete.promise.bind(py);
            let Some(promise) = extract_promise_id(promise_obj) else {
                return Err(
                    "CompletePromiseEffect.promise must carry _promise_handle.promise_id"
                        .to_string(),
                );
            };
            return Ok(Some(SchedulerEffect::CompletePromise {
                promise,
                value: Value::from_pyobject(complete.value.bind(py)),
            }));
        }

        if let Ok(fail) = obj.extract::<PyRef<'_, PyFailPromise>>() {
            let promise_obj = fail.promise.bind(py);
            let Some(promise) = extract_promise_id(promise_obj) else {
                return Err(
                    "FailPromiseEffect.promise must carry _promise_handle.promise_id".to_string(),
                );
            };
            let error = pyobject_to_exception(py, fail.error.bind(py));
            return Ok(Some(SchedulerEffect::FailPromise { promise, error }));
        }

        if let Ok(create) = obj.extract::<PyRef<'_, PyCreateSemaphore>>() {
            if create.permits < 1 {
                return Err("CreateSemaphoreEffect.permits must be >= 1".to_string());
            }
            return Ok(Some(SchedulerEffect::CreateSemaphore {
                permits: create.permits as u64,
            }));
        }

        if let Ok(acquire) = obj.extract::<PyRef<'_, PyAcquireSemaphore>>() {
            let semaphore_obj = acquire.semaphore.bind(py);
            let Some(semaphore_id) = extract_semaphore_id(semaphore_obj) else {
                return Err("AcquireSemaphoreEffect.semaphore must carry a numeric id".to_string());
            };
            return Ok(Some(SchedulerEffect::AcquireSemaphore { semaphore_id }));
        }

        if let Ok(release) = obj.extract::<PyRef<'_, PyReleaseSemaphore>>() {
            let semaphore_obj = release.semaphore.bind(py);
            let Some(semaphore_id) = extract_semaphore_id(semaphore_obj) else {
                return Err("ReleaseSemaphoreEffect.semaphore must carry a numeric id".to_string());
            };
            return Ok(Some(SchedulerEffect::ReleaseSemaphore { semaphore_id }));
        }

        if let Ok(done) = obj.extract::<PyRef<'_, PyTaskCompleted>>() {
            let task = {
                let task_obj = done.task.bind(py);
                if task_obj.is_none() {
                    None
                } else {
                    extract_task_id(task_obj)
                }
            }
            .or_else(|| {
                let task_id_obj = done.task_id.bind(py);
                if task_id_obj.is_none() {
                    None
                } else {
                    task_id_obj.extract::<u64>().ok().map(TaskId::from_raw)
                }
            })
            .ok_or_else(|| {
                "TaskCompletedEffect/SchedulerTaskCompleted requires task.task_id or task_id"
                    .to_string()
            })?;

            let result_obj = done.result.bind(py);
            let result = parse_task_completed_result(py, result_obj)?;
            return Ok(Some(SchedulerEffect::TaskCompleted { task, result }));
        }

        if obj.extract::<PyRef<'_, PyGetExecutionContext>>().is_ok() {
            return Ok(Some(SchedulerEffect::GetExecutionContext));
        }

        Ok(None)
    })
}

fn extract_waitable(obj: &Bound<'_, PyAny>) -> Option<Waitable> {
    let handle = obj.getattr("_handle").ok()?;
    let type_val = handle.get_item("type").ok()?;
    let type_str: String = type_val.extract().ok()?;
    match type_str.as_str() {
        "Task" => {
            let raw: u64 = handle.get_item("task_id").ok()?.extract().ok()?;
            Some(Waitable::Task(TaskId::from_raw(raw)))
        }
        "Promise" => {
            let raw: u64 = handle.get_item("promise_id").ok()?.extract().ok()?;
            Some(Waitable::Promise(PromiseId::from_raw(raw)))
        }
        "ExternalPromise" => {
            let raw: u64 = handle.get_item("promise_id").ok()?.extract().ok()?;
            Some(Waitable::ExternalPromise(PromiseId::from_raw(raw)))
        }
        _ => None,
    }
}

fn extract_promise_id(obj: &Bound<'_, PyAny>) -> Option<PromiseId> {
    let handle = obj.getattr("_promise_handle").ok()?;
    let raw: u64 = handle.get_item("promise_id").ok()?.extract().ok()?;
    Some(PromiseId::from_raw(raw))
}

fn extract_task_id(obj: &Bound<'_, PyAny>) -> Option<TaskId> {
    if let Ok(handle) = obj.getattr("_handle") {
        if let Ok(raw) = handle.get_item("task_id").and_then(|v| v.extract::<u64>()) {
            return Some(TaskId::from_raw(raw));
        }
    }
    None
}

fn extract_handlers_from_python(obj: &Bound<'_, PyAny>) -> Result<Vec<Handler>, String> {
    if obj.is_none() {
        return Ok(vec![]);
    }
    let mut handlers = Vec::new();
    for item in obj.try_iter().map_err(|e| e.to_string())? {
        let item = item.map_err(|e| e.to_string())?;
        if item.is_instance_of::<PyRustHandlerSentinel>() {
            let sentinel: PyRef<'_, PyRustHandlerSentinel> = item
                .extract::<PyRef<'_, PyRustHandlerSentinel>>()
                .map_err(|e| format!("{e:?}"))?;
            handlers.push(sentinel.factory_ref());
            continue;
        }

        if item.is_instance_of::<DoeffGeneratorFn>() {
            let dgfn = item
                .extract::<Py<DoeffGeneratorFn>>()
                .map_err(|e| format!("{e:?}"))?;
            handlers.push(Arc::new(PythonHandler::from_dgfn(dgfn)));
            continue;
        }

        if item.is_callable() {
            return Err(
                "Spawn handlers must be DoeffGeneratorFn or RustHandler sentinel".to_string(),
            );
        }

        let ty = item
            .get_type()
            .name()
            .map(|name| name.to_string())
            .unwrap_or_else(|_| "<unknown>".to_string());
        return Err(format!(
            "Spawn handlers must be DoeffGeneratorFn or RustHandler sentinel, got {ty}"
        ));
    }
    Ok(handlers)
}

fn parse_store_mode(obj: &Bound<'_, PyAny>) -> Result<StoreMode, String> {
    if obj.is_none() {
        return Ok(StoreMode::Shared);
    }
    if let Ok(mode) = obj.extract::<String>() {
        return match mode.to_lowercase().as_str() {
            "shared" => Ok(StoreMode::Shared),
            "isolated" => Ok(StoreMode::Isolated {
                merge: StoreMergePolicy::LogsOnly,
            }),
            other => Err(format!("unsupported store_mode '{other}'")),
        };
    }
    Ok(StoreMode::Shared)
}

fn pyobject_to_exception(py: Python<'_>, error_obj: &Bound<'_, PyAny>) -> PyException {
    let exc_type = error_obj.get_type().into_any().unbind();
    let exc_value = error_obj.clone().unbind();
    let exc_tb = py.None();
    PyException::new(exc_type, exc_value, Some(exc_tb))
}

fn task_cancelled_error() -> PyException {
    Python::attach(|py| {
        let message = "Task was cancelled";
        let cancelled = (|| -> PyResult<Bound<'_, PyAny>> {
            let spawn_mod = py.import("doeff.effects.spawn")?;
            let cls = spawn_mod.getattr("TaskCancelledError")?;
            cls.call1((message,))
        })();

        match cancelled {
            Ok(exc_obj) => pyobject_to_exception(py, &exc_obj),
            Err(_) => PyException::runtime_error(message.to_string()),
        }
    })
}

fn make_python_semaphore_value(
    semaphore_id: u64,
    scheduler_state_id: u64,
) -> Result<Value, PyException> {
    Python::attach(|py| {
        let semaphore_mod = py.import("doeff.effects.semaphore").map_err(|e| {
            PyException::runtime_error(format!(
                "failed to import semaphore module while creating Semaphore handle: {e}"
            ))
        })?;
        let semaphore_cls = semaphore_mod.getattr("Semaphore").map_err(|e| {
            PyException::runtime_error(format!(
                "failed to resolve Semaphore class while creating Semaphore handle: {e}"
            ))
        })?;
        let semaphore = semaphore_cls
            .call1((semaphore_id, scheduler_state_id, true))
            .map_err(|e| {
                PyException::runtime_error(format!(
                    "failed to instantiate Semaphore({semaphore_id}): {e}"
                ))
            })?;
        Ok(Value::Python(semaphore.unbind()))
    })
}

fn unknown_semaphore_error(semaphore_id: u64) -> PyException {
    PyException::runtime_error(format!("unknown semaphore id {semaphore_id}"))
}

fn scheduler_internal_error(message: impl Into<String>) -> PyException {
    PyException::runtime_error(format!("scheduler internal error: {}", message.into()))
}

// ---------------------------------------------------------------------------
// SchedulerState implementation
// ---------------------------------------------------------------------------

impl SchedulerState {
    pub fn new() -> Self {
        SchedulerState {
            ready: VecDeque::new(),
            ready_waiters: VecDeque::new(),
            tasks: HashMap::new(),
            task_metadata: HashMap::new(),
            promises: HashMap::new(),
            semaphores: HashMap::new(),
            waiters: HashMap::new(),
            external_completion_queue: None,
            cancel_requested: HashSet::new(),
            next_task: 0,
            next_promise: 0,
            next_semaphore: 1,
            current_task: None,
            execution_context_task_override: None,
            state_id: NEXT_SCHEDULER_STATE_ID.fetch_add(1, Ordering::Relaxed),
        }
    }

    pub fn state_id(&self) -> u64 {
        self.state_id
    }

    pub fn process_semaphore_drop_notifications(&mut self) {
        let dropped = {
            let mut notifications = semaphore_drop_notifications()
                .lock()
                .expect("Scheduler lock poisoned");
            notifications.remove(&self.state_id)
        };
        let Some(dropped) = dropped else {
            return;
        };

        // Duplicate notifications are possible when multiple temporary references
        // to the same Semaphore object are collected around the same time.
        let mut unique = HashSet::new();
        for semaphore_id in dropped {
            if unique.insert(semaphore_id) {
                self.remove_semaphore(semaphore_id);
            }
        }
    }

    pub fn ensure_external_completion_queue(&mut self) -> Result<PyShared, PyException> {
        if let Some(queue) = &self.external_completion_queue {
            return Ok(queue.clone());
        }

        Python::attach(|py| {
            let queue_mod = py.import("queue").map_err(|e| {
                PyException::runtime_error(format!(
                    "failed to import Python queue module for ExternalPromise bridge: {e}"
                ))
            })?;
            let queue_type = queue_mod.getattr("Queue").map_err(|e| {
                PyException::runtime_error(format!(
                    "failed to resolve queue.Queue for ExternalPromise bridge: {e}"
                ))
            })?;
            let queue_obj = queue_type.call0().map_err(|e| {
                PyException::runtime_error(format!(
                    "failed to create completion queue for ExternalPromise bridge: {e}"
                ))
            })?;

            let shared = PyShared::new(queue_obj.unbind());
            self.external_completion_queue = Some(shared.clone());
            Ok(shared)
        })
    }

    pub fn mark_promise_done(&mut self, promise_id: PromiseId, result: Result<Value, PyException>) {
        self.promises.insert(promise_id, PromiseState::Done(result));
        self.wake_waiters(Waitable::Promise(promise_id));
        self.wake_waiters(Waitable::ExternalPromise(promise_id));
    }

    fn has_external_waiters(&self) -> bool {
        self.waiters
            .keys()
            .any(|item| matches!(item, Waitable::ExternalPromise(_)))
    }

    fn parse_external_completion_item(
        py: Python<'_>,
        item: &Bound<'_, PyAny>,
    ) -> Result<(PromiseId, Result<Value, PyException>), PyException> {
        let tuple = item.cast::<PyTuple>().map_err(|_| {
            PyException::type_error(
                "ExternalPromise completion queue item must be a tuple (promise_id, value, error)",
            )
        })?;
        if tuple.len() != 3 {
            return Err(PyException::type_error(
                "ExternalPromise completion queue item must have exactly 3 elements",
            ));
        }

        let pid_raw = tuple
            .get_item(0)
            .map_err(|e| {
                PyException::type_error(format!(
                    "failed to read promise_id from ExternalPromise completion tuple: {e}"
                ))
            })?
            .extract::<u64>()
            .map_err(|_| {
                PyException::type_error(
                    "ExternalPromise completion tuple promise_id must be an integer",
                )
            })?;
        let value_obj = tuple.get_item(1).map_err(|e| {
            PyException::type_error(format!(
                "failed to read value from ExternalPromise completion tuple: {e}"
            ))
        })?;
        let error_obj = tuple.get_item(2).map_err(|e| {
            PyException::type_error(format!(
                "failed to read error from ExternalPromise completion tuple: {e}"
            ))
        })?;

        let result = if error_obj.is_none() {
            Ok(Value::from_pyobject(&value_obj))
        } else {
            Err(pyobject_to_exception(py, &error_obj))
        };

        Ok((PromiseId::from_raw(pid_raw), result))
    }

    fn drain_external_completions_nonblocking(&mut self) -> Result<(), PyException> {
        let Some(queue) = self.external_completion_queue.clone() else {
            return Ok(());
        };

        Python::attach(|py| {
            let queue_obj = queue.bind(py);
            let queue_mod = py.import("queue").map_err(|e| {
                PyException::runtime_error(format!(
                    "failed to import Python queue module while draining ExternalPromise completions: {e}"
                ))
            })?;
            let empty_type = queue_mod.getattr("Empty").map_err(|e| {
                PyException::runtime_error(format!(
                    "failed to resolve queue.Empty while draining ExternalPromise completions: {e}"
                ))
            })?;

            loop {
                let item = match queue_obj.call_method0("get_nowait") {
                    Ok(v) => v,
                    Err(err) => {
                        if err.matches(py, &empty_type).unwrap_or(false) {
                            break;
                        }
                        return Err(PyException::runtime_error(format!(
                            "failed while draining ExternalPromise completion queue: {err}"
                        )));
                    }
                };
                let (promise_id, result) = Self::parse_external_completion_item(py, &item)?;
                self.mark_promise_done(promise_id, result);
            }

            Ok(())
        })
    }

    fn block_until_external_completion(&mut self) -> Result<(), PyException> {
        let Some(queue) = self.external_completion_queue.clone() else {
            return Ok(());
        };

        // Keep timeout short so async_run can yield back to the caller event loop
        // while waiting on external completions.
        const TIMEOUT_SECONDS: f64 = 0.001;

        Python::attach(|py| {
            let queue_obj = queue.bind(py);
            let queue_mod = py.import("queue").map_err(|e| {
                PyException::runtime_error(format!(
                    "failed to import Python queue module while blocking on ExternalPromise: {e}"
                ))
            })?;
            let empty_type = queue_mod.getattr("Empty").map_err(|e| {
                PyException::runtime_error(format!(
                    "failed to resolve queue.Empty while blocking on ExternalPromise: {e}"
                ))
            })?;

            let item = match queue_obj.call_method1("get", (true, TIMEOUT_SECONDS)) {
                Ok(v) => v,
                Err(err) => {
                    // Timeout: queue.Empty raised. Return Ok so caller can re-check state.
                    if err.matches(py, &empty_type).unwrap_or(false) {
                        return Ok(());
                    }
                    return Err(PyException::runtime_error(format!(
                        "failed while waiting on ExternalPromise completion queue: {err}"
                    )));
                }
            };

            let (promise_id, result) = Self::parse_external_completion_item(py, &item)?;
            self.mark_promise_done(promise_id, result);
            Ok(())
        })
    }

    pub fn alloc_task_id(&mut self) -> TaskId {
        let id = TaskId::from_raw(self.next_task);
        self.next_task += 1;
        id
    }

    pub fn alloc_promise_id(&mut self) -> PromiseId {
        let id = PromiseId::from_raw(self.next_promise);
        self.next_promise += 1;
        id
    }

    pub fn alloc_semaphore_id(&mut self) -> u64 {
        let id = self.next_semaphore;
        self.next_semaphore += 1;
        id
    }

    pub fn create_semaphore(&mut self, permits: u64) -> u64 {
        let semaphore_id = self.alloc_semaphore_id();
        self.semaphores.insert(
            semaphore_id,
            SemaphoreRuntimeState {
                max_permits: permits,
                available_permits: permits,
                waiters: VecDeque::new(),
                holders: HashMap::new(),
            },
        );
        semaphore_id
    }

    pub fn remove_semaphore(&mut self, semaphore_id: u64) {
        let Some(semaphore) = self.semaphores.remove(&semaphore_id) else {
            return;
        };

        if !semaphore.waiters.is_empty() {
            eprintln!(
                "warning: semaphore {semaphore_id} dropped with {} pending waiter(s); cancelling waiters",
                semaphore.waiters.len()
            );
        }

        let waiters: Vec<_> = semaphore.waiters.into_iter().collect();
        let blocked_tasks: HashSet<_> = waiters
            .iter()
            .filter_map(|waiter| waiter.waiting_task)
            .collect();

        for task_id in blocked_tasks {
            self.finalize_task_cancellation(task_id);
        }

        for waiter in waiters {
            if waiter.waiting_task.is_some() {
                // Promise resolution is no longer observed once the owning task is cancelled.
                self.promises.insert(
                    waiter.promise,
                    PromiseState::Done(Err(task_cancelled_error())),
                );
            } else {
                self.mark_promise_done(waiter.promise, Err(task_cancelled_error()));
            }
        }
    }

    fn remove_semaphore_waiters_for_task(&mut self, task_id: TaskId) -> Vec<PromiseId> {
        let mut removed = Vec::new();
        for semaphore in self.semaphores.values_mut() {
            let mut retained = VecDeque::new();
            while let Some(waiter) = semaphore.waiters.pop_front() {
                if waiter.waiting_task == Some(task_id) {
                    removed.push(waiter.promise);
                } else {
                    retained.push_back(waiter);
                }
            }
            semaphore.waiters = retained;
        }
        removed
    }

    fn finalize_task_cancellation(&mut self, task_id: TaskId) {
        let cont_id = match self.tasks.get(&task_id) {
            Some(TaskState::Pending { cont, .. }) => Some(cont.cont_id),
            _ => None,
        };
        if let Some(cont_id) = cont_id {
            self.clear_waiters_for_continuation(cont_id);
        }

        self.ready.retain(|queued| *queued != task_id);
        self.current_task = self.current_task.filter(|running| *running != task_id);
        self.cancel_requested.remove(&task_id);

        let cancelled_waiters = self.remove_semaphore_waiters_for_task(task_id);
        for promise_id in cancelled_waiters {
            self.mark_promise_done(promise_id, Err(task_cancelled_error()));
        }

        self.mark_task_done(task_id, Err(task_cancelled_error()))
            .expect("finalize_task_cancellation: task must exist while finalizing cancellation");
        self.wake_waiters(Waitable::Task(task_id));
    }

    pub fn request_task_cancellation(&mut self, task_id: TaskId) {
        let Some(task_state) = self.tasks.get(&task_id) else {
            return;
        };

        if matches!(task_state, TaskState::Done { .. }) {
            return;
        }

        if self.current_task == Some(task_id) {
            self.cancel_requested.insert(task_id);
            return;
        }

        self.finalize_task_cancellation(task_id);
    }

    pub fn cancel_requested_for_running_task(&self) -> Option<TaskId> {
        let running = self.current_task?;
        self.cancel_requested.contains(&running).then_some(running)
    }

    pub fn apply_running_task_cancellation_if_requested(&mut self) -> bool {
        let Some(task_id) = self.cancel_requested_for_running_task() else {
            return false;
        };
        self.finalize_task_cancellation(task_id);
        true
    }

    pub fn acquire_semaphore(
        &mut self,
        semaphore_id: u64,
    ) -> Result<Option<PromiseId>, PyException> {
        let owner = self.current_task;

        let can_acquire = {
            let semaphore = self
                .semaphores
                .get(&semaphore_id)
                .ok_or_else(|| unknown_semaphore_error(semaphore_id))?;
            semaphore.available_permits > 0 && semaphore.waiters.is_empty()
        };

        if can_acquire {
            if let Some(semaphore) = self.semaphores.get_mut(&semaphore_id) {
                semaphore.available_permits -= 1;
                let held = semaphore.holders.entry(owner).or_insert(0);
                *held += 1;
                return Ok(None);
            }
            return Err(unknown_semaphore_error(semaphore_id));
        }

        let self_deadlock = {
            let semaphore = self
                .semaphores
                .get(&semaphore_id)
                .ok_or_else(|| unknown_semaphore_error(semaphore_id))?;
            semaphore.max_permits == 1 && semaphore.holders.get(&owner).copied().unwrap_or(0) > 0
        };
        if self_deadlock {
            return Err(PyException::runtime_error(
                "circular lazy Ask dependency detected".to_string(),
            ));
        }

        let waiter_promise = self.alloc_promise_id();
        self.promises.insert(waiter_promise, PromiseState::Pending);
        let waiting_task = self.current_task;
        if let Some(semaphore) = self.semaphores.get_mut(&semaphore_id) {
            semaphore.waiters.push_back(SemaphoreWaiter {
                promise: waiter_promise,
                waiting_task,
            });
            Ok(Some(waiter_promise))
        } else {
            Err(unknown_semaphore_error(semaphore_id))
        }
    }

    pub fn release_semaphore(&mut self, semaphore_id: u64) -> Result<(), PyException> {
        let owner = self.current_task;
        let released_one = {
            let semaphore = self
                .semaphores
                .get_mut(&semaphore_id)
                .ok_or_else(|| unknown_semaphore_error(semaphore_id))?;
            if let Some(held) = semaphore.holders.get_mut(&owner) {
                *held -= 1;
                if *held == 0 {
                    semaphore.holders.remove(&owner);
                }
                true
            } else {
                false
            }
        };
        if !released_one {
            return Err(PyException::runtime_error(
                "semaphore released too many times".to_string(),
            ));
        }

        let next_waiter = if let Some(semaphore) = self.semaphores.get_mut(&semaphore_id) {
            semaphore.waiters.pop_front()
        } else {
            return Err(unknown_semaphore_error(semaphore_id));
        };

        if let Some(waiter) = next_waiter {
            if let Some(semaphore) = self.semaphores.get_mut(&semaphore_id) {
                let held = semaphore.holders.entry(waiter.waiting_task).or_insert(0);
                *held += 1;
            } else {
                return Err(unknown_semaphore_error(semaphore_id));
            }
            self.mark_promise_done(waiter.promise, Ok(Value::Unit));
            return Ok(());
        }

        let over_release = {
            let semaphore = self
                .semaphores
                .get(&semaphore_id)
                .ok_or_else(|| unknown_semaphore_error(semaphore_id))?;
            semaphore.available_permits >= semaphore.max_permits
        };
        if over_release {
            return Err(PyException::runtime_error(
                "semaphore released too many times".to_string(),
            ));
        }

        if let Some(semaphore) = self.semaphores.get_mut(&semaphore_id) {
            semaphore.available_permits += 1;
            return Ok(());
        }
        Err(unknown_semaphore_error(semaphore_id))
    }

    pub fn save_task_store(
        &mut self,
        task_id: TaskId,
        store: &RustStore,
    ) -> Result<(), PyException> {
        let state = self.tasks.get_mut(&task_id).ok_or_else(|| {
            scheduler_internal_error(format!("save_task_store: task {} not found", task_id.raw()))
        })?;
        match state {
            TaskState::Pending {
                store:
                    TaskStore::Isolated {
                        store: ref mut task_store,
                        ..
                    },
                ..
            } => {
                *task_store = store.clone();
                Ok(())
            }
            TaskState::Pending {
                store: TaskStore::Shared,
                ..
            } => Ok(()),
            TaskState::Done { .. } => Err(scheduler_internal_error(format!(
                "save_task_store: task {} is already done",
                task_id.raw()
            ))),
        }
    }

    pub fn load_task_store(
        &self,
        task_id: TaskId,
        store: &mut RustStore,
    ) -> Result<(), PyException> {
        let state = self.tasks.get(&task_id).ok_or_else(|| {
            scheduler_internal_error(format!("load_task_store: task {} not found", task_id.raw()))
        })?;
        let task_store = match state {
            TaskState::Pending { store, .. } => store,
            TaskState::Done { store, .. } => store,
        };
        if let TaskStore::Isolated {
            store: task_store, ..
        } = task_store
        {
            *store = task_store.clone();
        }
        Ok(())
    }

    pub fn mark_task_done(
        &mut self,
        task_id: TaskId,
        result: Result<Value, PyException>,
    ) -> Result<(), PyException> {
        self.cancel_requested.remove(&task_id);
        let state = self.tasks.remove(&task_id).ok_or_else(|| {
            scheduler_internal_error(format!("mark_task_done: task {} not found", task_id.raw()))
        })?;
        let task_store = match state {
            TaskState::Pending { store, .. } => store,
            TaskState::Done { store, .. } => store,
        };
        self.tasks.insert(
            task_id,
            TaskState::Done {
                result,
                store: task_store,
            },
        );
        Ok(())
    }

    pub fn wake_waiters(&mut self, waitable: Waitable) {
        let Some(waiters_for_item) = self.waiters.remove(&waitable) else {
            return;
        };

        for waiter in waiters_for_item {
            let waiter_id = waiter.continuation.cont_id;
            let already_ready = self
                .ready_waiters
                .iter()
                .any(|w| w.continuation.cont_id == waiter_id);
            if already_ready {
                continue;
            }

            let ready = match waiter.mode {
                WaitMode::All => self.all_done(&waiter.items),
                WaitMode::Any => self.any_done(&waiter.items),
            };

            if ready {
                for pending in self.waiters.values_mut() {
                    pending.retain(|w| w.continuation.cont_id != waiter_id);
                }
                self.ready_waiters.push_back(waiter);
            }
        }
    }

    fn clear_waiters_for_continuation(&mut self, cont_id: ContId) {
        self.ready_waiters
            .retain(|waiter| waiter.continuation.cont_id != cont_id);
        for pending in self.waiters.values_mut() {
            pending.retain(|waiter| waiter.continuation.cont_id != cont_id);
        }
    }

    pub fn task_cont(&self, task_id: TaskId) -> Option<Continuation> {
        match self.tasks.get(&task_id) {
            Some(TaskState::Pending { cont, .. }) => Some(cont.clone()),
            _ => None,
        }
    }

    pub fn try_collect(&self, items: &[Waitable]) -> Option<Value> {
        let mut results = Vec::new();
        for item in items {
            match item {
                Waitable::Task(task_id) => match self.tasks.get(task_id) {
                    Some(TaskState::Done { result: Ok(v), .. }) => results.push(v.clone()),
                    Some(TaskState::Done { result: Err(_), .. }) => return None,
                    _ => return None,
                },
                Waitable::Promise(pid) | Waitable::ExternalPromise(pid) => {
                    match self.promises.get(pid) {
                        Some(PromiseState::Done(Ok(v))) => results.push(v.clone()),
                        Some(PromiseState::Done(Err(_))) => return None,
                        _ => return None,
                    }
                }
            }
        }
        Some(Value::List(results))
    }

    pub fn try_race(&self, items: &[Waitable]) -> Option<Value> {
        for item in items {
            match item {
                Waitable::Task(task_id) => {
                    if let Some(TaskState::Done { result: Ok(v), .. }) = self.tasks.get(task_id) {
                        return Some(v.clone());
                    }
                }
                Waitable::Promise(pid) | Waitable::ExternalPromise(pid) => {
                    if let Some(PromiseState::Done(Ok(v))) = self.promises.get(pid) {
                        return Some(v.clone());
                    }
                }
            }
        }
        None
    }

    pub fn wait_on_all(&mut self, items: &[Waitable], k: Continuation, store: &RustStore) {
        let waiter = WaitRequest {
            continuation: k,
            items: items.to_vec(),
            mode: WaitMode::All,
            waiting_task: self.current_task,
            waiting_store: store.clone(),
        };

        for item in items {
            if !self.is_done(*item) {
                self.waiters.entry(*item).or_default().push(waiter.clone());
            }
        }
    }

    pub fn wait_on_any(&mut self, items: &[Waitable], k: Continuation, store: &RustStore) {
        let waiter = WaitRequest {
            continuation: k,
            items: items.to_vec(),
            mode: WaitMode::Any,
            waiting_task: self.current_task,
            waiting_store: store.clone(),
        };

        for item in items {
            if !self.is_done(*item) {
                self.waiters.entry(*item).or_default().push(waiter.clone());
            }
        }
    }

    fn is_done(&self, item: Waitable) -> bool {
        match item {
            Waitable::Task(tid) => matches!(self.tasks.get(&tid), Some(TaskState::Done { .. })),
            Waitable::Promise(pid) | Waitable::ExternalPromise(pid) => {
                matches!(self.promises.get(&pid), Some(PromiseState::Done(_)))
            }
        }
    }

    fn all_done(&self, items: &[Waitable]) -> bool {
        items.iter().all(|item| self.is_done(*item))
    }

    fn any_done(&self, items: &[Waitable]) -> bool {
        items.iter().any(|item| self.is_done(*item))
    }

    fn collect_all_result(&mut self, items: &[Waitable]) -> Option<Result<Value, PyException>> {
        self.execution_context_task_override = None;
        let mut results = Vec::with_capacity(items.len());
        for item in items {
            match item {
                Waitable::Task(task_id) => {
                    let task_result = match self.tasks.get(task_id) {
                        Some(TaskState::Done { result, .. }) => result.clone(),
                        _ => return None,
                    };
                    match task_result {
                        Ok(value) => results.push(value),
                        Err(error) => {
                            self.execution_context_task_override = Some(*task_id);
                            return Some(Err(error));
                        }
                    }
                }
                Waitable::Promise(pid) | Waitable::ExternalPromise(pid) => {
                    match self.promises.get(pid) {
                        Some(PromiseState::Done(Ok(v))) => results.push(v.clone()),
                        Some(PromiseState::Done(Err(e))) => return Some(Err(e.clone())),
                        _ => return None,
                    }
                }
            }
        }
        Some(Ok(Value::List(results)))
    }

    fn collect_any_result(&mut self, items: &[Waitable]) -> Option<Result<Value, PyException>> {
        self.execution_context_task_override = None;
        for item in items {
            match item {
                Waitable::Task(task_id) => {
                    let task_result = match self.tasks.get(task_id) {
                        Some(TaskState::Done { result, .. }) => result.clone(),
                        _ => continue,
                    };
                    if task_result.is_err() {
                        self.execution_context_task_override = Some(*task_id);
                    }
                    return Some(task_result);
                }
                Waitable::Promise(pid) | Waitable::ExternalPromise(pid) => {
                    if let Some(PromiseState::Done(result)) = self.promises.get(pid) {
                        return Some(result.clone());
                    }
                }
            }
        }
        None
    }

    pub fn merge_task_logs(&self, task_id: TaskId, store: &mut RustStore) {
        if let Some(state) = self.tasks.get(&task_id) {
            let task_store = match state {
                TaskState::Pending { store, .. } => store,
                TaskState::Done { store, .. } => store,
            };
            if let TaskStore::Isolated {
                store: task_store,
                merge: StoreMergePolicy::LogsOnly,
            } = task_store
            {
                store.log.extend(task_store.log.iter().cloned());
            }
        }
    }

    pub fn merge_gather_logs(&self, items: &[Waitable], store: &mut RustStore) {
        for item in items {
            if let Waitable::Task(task_id) = item {
                self.merge_task_logs(*task_id, store);
            }
        }
    }

    /// Transfer to the next ready task, or resume k if no tasks are ready.
    ///
    /// Per spec (SPEC-008 L1434-1447): saves the current task's store before
    /// switching and loads the new task's store after switching.
    pub fn transfer_next_or(&mut self, k: Continuation, store: &mut RustStore) -> ASTStreamStep {
        loop {
            self.process_semaphore_drop_notifications();

            if let Err(error) = self.drain_external_completions_nonblocking() {
                return ASTStreamStep::Throw(error);
            }

            if let Some(task_id) = self.ready.pop_front() {
                if self.cancel_requested.contains(&task_id) {
                    self.finalize_task_cancellation(task_id);
                    continue;
                }
                let Some(task_k) = self.task_cont(task_id) else {
                    return ASTStreamStep::Throw(scheduler_internal_error(format!(
                        "transfer_next_or: ready task {} has no continuation",
                        task_id.raw()
                    )));
                };
                // Save current task's store before switching away
                if let Some(old_id) = self.current_task {
                    if let Err(error) = self.save_task_store(old_id, store) {
                        return ASTStreamStep::Throw(error);
                    }
                }
                // Load new task's store
                if let Err(error) = self.load_task_store(task_id, store) {
                    return ASTStreamStep::Throw(error);
                }
                self.current_task = Some(task_id);
                return transfer_to_continuation(task_k, Value::Unit);
            }

            let ready_waiter_scan_len = self.ready_waiters.len();
            for _ in 0..ready_waiter_scan_len {
                let Some(waiter) = self.ready_waiters.pop_front() else {
                    break;
                };

                // ready_waiters are owner-bound. Do not resume a foreign
                // continuation from this transfer_next_or invocation.
                if waiter.continuation.cont_id != k.cont_id {
                    self.ready_waiters.push_back(waiter);
                    continue;
                }

                match waiter.mode {
                    WaitMode::All => match self.collect_all_result(&waiter.items) {
                        Some(Ok(value)) => {
                            let cont_id = waiter.continuation.cont_id;
                            self.ready_waiters
                                .retain(|pending| pending.continuation.cont_id != cont_id);
                            for pending in self.waiters.values_mut() {
                                pending.retain(|w| w.continuation.cont_id != cont_id);
                            }

                            if let Some(waiting_task) = waiter.waiting_task {
                                if let Err(error) = self.load_task_store(waiting_task, store) {
                                    return ASTStreamStep::Throw(error);
                                }
                                self.current_task = Some(waiting_task);
                            } else {
                                *store = waiter.waiting_store.clone();
                            }
                            if waiter.items.len() > 1 {
                                self.merge_gather_logs(&waiter.items, store);
                            }
                            return resume_to_continuation(waiter.continuation, value);
                        }
                        Some(Err(error)) => {
                            let cont_id = waiter.continuation.cont_id;
                            self.ready_waiters
                                .retain(|pending| pending.continuation.cont_id != cont_id);
                            for pending in self.waiters.values_mut() {
                                pending.retain(|w| w.continuation.cont_id != cont_id);
                            }

                            if let Some(waiting_task) = waiter.waiting_task {
                                if let Err(error) = self.load_task_store(waiting_task, store) {
                                    return ASTStreamStep::Throw(error);
                                }
                                self.current_task = Some(waiting_task);
                            } else {
                                *store = waiter.waiting_store.clone();
                            }
                            return throw_to_continuation(waiter.continuation, error);
                        }
                        None => continue,
                    },
                    WaitMode::Any => match self.collect_any_result(&waiter.items) {
                        Some(Ok(value)) => {
                            let cont_id = waiter.continuation.cont_id;
                            self.ready_waiters
                                .retain(|pending| pending.continuation.cont_id != cont_id);
                            for pending in self.waiters.values_mut() {
                                pending.retain(|w| w.continuation.cont_id != cont_id);
                            }

                            if let Some(waiting_task) = waiter.waiting_task {
                                if let Err(error) = self.load_task_store(waiting_task, store) {
                                    return ASTStreamStep::Throw(error);
                                }
                                self.current_task = Some(waiting_task);
                            } else {
                                *store = waiter.waiting_store.clone();
                            }
                            return resume_to_continuation(waiter.continuation, value);
                        }
                        Some(Err(error)) => {
                            let cont_id = waiter.continuation.cont_id;
                            self.ready_waiters
                                .retain(|pending| pending.continuation.cont_id != cont_id);
                            for pending in self.waiters.values_mut() {
                                pending.retain(|w| w.continuation.cont_id != cont_id);
                            }

                            if let Some(waiting_task) = waiter.waiting_task {
                                if let Err(error) = self.load_task_store(waiting_task, store) {
                                    return ASTStreamStep::Throw(error);
                                }
                                self.current_task = Some(waiting_task);
                            } else {
                                *store = waiter.waiting_store.clone();
                            }
                            return throw_to_continuation(waiter.continuation, error);
                        }
                        None => continue,
                    },
                }
            }

            if self.has_external_waiters() {
                if let Err(error) = self.block_until_external_completion() {
                    return ASTStreamStep::Throw(error);
                }
                continue;
            }

            // No ready tasks, resume the caller
            return resume_to_continuation(k, Value::Unit);
        }
    }
}

impl Drop for SchedulerState {
    fn drop(&mut self) {
        if let Ok(mut notifications) = semaphore_drop_notifications().lock() {
            notifications.remove(&self.state_id);
        }
        if let Ok(mut registry) = scheduler_state_registry().lock() {
            registry.remove(&self.state_id);
        }
    }
}

// ---------------------------------------------------------------------------
// SchedulerPhase (internal to scheduler program)
// ---------------------------------------------------------------------------

#[derive(Debug)]
enum SchedulerPhase {
    Idle,
    SpawnAwaitTraceback {
        k_user: Continuation,
        effect: DispatchEffect,
    },
    SpawnAwaitHandlers {
        k_user: Continuation,
        program: Py<PyAny>,
        store_mode: StoreMode,
        store_snapshot: Option<RustStore>,
        spawn_site: Option<SpawnSite>,
    },
    SpawnAwaitContinuation {
        k_user: Continuation,
        store_mode: StoreMode,
        store_snapshot: Option<RustStore>,
        spawn_site: Option<SpawnSite>,
    },
    Driving {
        k_user: Continuation,
        items: Vec<Waitable>,
        mode: WaitMode,
        running_task: Option<TaskId>,
        waiting_task: Option<TaskId>,
        waiting_store: RustStore,
    },
}

// ---------------------------------------------------------------------------
// SchedulerProgram + ASTStreamProgram impl
// ---------------------------------------------------------------------------

pub struct SchedulerProgram {
    state: Arc<Mutex<SchedulerState>>,
    phase: SchedulerPhase,
}

impl std::fmt::Debug for SchedulerProgram {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SchedulerProgram")
            .field("phase", &self.phase)
            .finish()
    }
}

impl SchedulerProgram {
    pub fn new(state: Arc<Mutex<SchedulerState>>) -> Self {
        SchedulerProgram {
            state,
            phase: SchedulerPhase::Idle,
        }
    }

    fn current_phase_name(&self) -> &'static str {
        match self.phase {
            SchedulerPhase::Idle => "Idle",
            SchedulerPhase::SpawnAwaitTraceback { .. } => "SpawnAwaitTraceback",
            SchedulerPhase::SpawnAwaitHandlers { .. } => "SpawnAwaitHandlers",
            SchedulerPhase::SpawnAwaitContinuation { .. } => "SpawnAwaitContinuation",
            SchedulerPhase::Driving { .. } => "Driving",
        }
    }

    fn handle_gather(
        &mut self,
        k_user: Continuation,
        items: Vec<Waitable>,
        store: &mut RustStore,
    ) -> ASTStreamStep {
        let mut state = self.state.lock().expect("Scheduler lock poisoned");
        let waiting_task = state.current_task;
        let waiting_store = store.clone();
        if let Some(aggregate) = state.collect_all_result(&items) {
            state.clear_waiters_for_continuation(k_user.cont_id);
            return match aggregate {
                Ok(results) => {
                    if items.len() > 1 {
                        state.merge_gather_logs(&items, store);
                    }
                    resume_to_continuation(k_user, results)
                }
                Err(error) => throw_to_continuation(k_user, error),
            };
        }
        state.wait_on_all(&items, k_user.clone(), store);
        let step = state.transfer_next_or(k_user.clone(), store);
        let running_task = state.current_task;
        let resumed_waiting_owner = step_targets_continuation(&step, &k_user);
        if running_task.is_none() || resumed_waiting_owner {
            self.phase = SchedulerPhase::Idle;
        } else {
            self.phase = SchedulerPhase::Driving {
                k_user,
                items,
                mode: WaitMode::All,
                running_task,
                waiting_task,
                waiting_store,
            };
        }
        step
    }

    fn handle_race(
        &mut self,
        k_user: Continuation,
        items: Vec<Waitable>,
        store: &mut RustStore,
    ) -> ASTStreamStep {
        let mut state = self.state.lock().expect("Scheduler lock poisoned");
        let waiting_task = state.current_task;
        let waiting_store = store.clone();
        if let Some(first) = state.collect_any_result(&items) {
            state.clear_waiters_for_continuation(k_user.cont_id);
            return match first {
                Ok(value) => resume_to_continuation(k_user, value),
                Err(error) => throw_to_continuation(k_user, error),
            };
        }
        state.wait_on_any(&items, k_user.clone(), store);
        let step = state.transfer_next_or(k_user.clone(), store);
        let running_task = state.current_task;
        let resumed_waiting_owner = step_targets_continuation(&step, &k_user);
        if running_task.is_none() || resumed_waiting_owner {
            self.phase = SchedulerPhase::Idle;
        } else {
            self.phase = SchedulerPhase::Driving {
                k_user,
                items,
                mode: WaitMode::Any,
                running_task,
                waiting_task,
                waiting_store,
            };
        }
        step
    }

    fn handle_get_execution_context(&mut self, k_user: Continuation) -> ASTStreamStep {
        let spawn_boundary = {
            let mut state = self.state.lock().expect("Scheduler lock poisoned");
            let task_for_context = state
                .execution_context_task_override
                .take()
                .or(state.current_task);
            task_for_context.and_then(|task_id| {
                state.task_metadata.get(&task_id).map(|metadata| {
                    (
                        task_id.raw(),
                        metadata.parent_task.map(|parent| parent.raw()),
                        metadata.spawn_site.clone(),
                    )
                })
            })
        };

        Python::attach(|py| {
            let context = match make_execution_context_object(py) {
                Ok(ctx) => ctx,
                Err(err) => {
                    let err_obj = err.value(py).clone().into_any().unbind();
                    return ASTStreamStep::Throw(pyobject_to_exception(py, err_obj.bind(py)));
                }
            };

            if let Some((task_id, parent_task, spawn_site)) = spawn_boundary {
                let result = (|| -> PyResult<()> {
                    let entry = PyDict::new(py);
                    entry.set_item("kind", "spawn_boundary")?;
                    entry.set_item("task_id", task_id)?;
                    match parent_task {
                        Some(parent_task) => entry.set_item("parent_task", parent_task)?,
                        None => entry.set_item("parent_task", py.None())?,
                    }
                    match spawn_site {
                        Some(site) => {
                            let site_dict = PyDict::new(py);
                            site_dict.set_item("function_name", site.function_name)?;
                            site_dict.set_item("source_file", site.source_file)?;
                            site_dict.set_item("source_line", site.source_line)?;
                            entry.set_item("spawn_site", site_dict)?;
                        }
                        None => entry.set_item("spawn_site", py.None())?,
                    }
                    let add = context.bind(py).getattr("add")?;
                    add.call1((entry,))?;
                    Ok(())
                })();

                if let Err(err) = result {
                    let err_obj = err.value(py).clone().into_any().unbind();
                    return ASTStreamStep::Throw(pyobject_to_exception(py, err_obj.bind(py)));
                }
            }

            resume_to_continuation(k_user, Value::Python(context))
        })
    }

    fn continue_driving(
        &mut self,
        outcome: Result<Value, PyException>,
        k_user: Continuation,
        items: Vec<Waitable>,
        mode: WaitMode,
        running_task: Option<TaskId>,
        waiting_task: Option<TaskId>,
        waiting_store: RustStore,
        store: &mut RustStore,
    ) -> ASTStreamStep {
        let mut state = self.state.lock().expect("Scheduler lock poisoned");

        let Some(task_id) = running_task else {
            return ASTStreamStep::Throw(PyException::runtime_error(
                "scheduler resumed/thrown without current running task",
            ));
        };

        if state.current_task == Some(task_id) {
            state.current_task = None;
        }

        if let Err(error) = state.save_task_store(task_id, store) {
            return ASTStreamStep::Throw(error);
        }
        if let Err(error) = state.mark_task_done(task_id, outcome) {
            return ASTStreamStep::Throw(error);
        }

        // If this continuation was previously queued as a waiter, remove stale
        // entries before wake-up processing. Otherwise it can be resumed once
        // directly here and then resumed again later from ready_waiters,
        // triggering one-shot continuation violations.
        let waiting_cont_id = k_user.cont_id;
        state.clear_waiters_for_continuation(waiting_cont_id);

        state.wake_waiters(Waitable::Task(task_id));

        match mode {
            WaitMode::All => {
                if let Some(aggregate) = state.collect_all_result(&items) {
                    return match aggregate {
                        Ok(value) => {
                            state.clear_waiters_for_continuation(waiting_cont_id);

                            if let Some(waiting_task) = waiting_task {
                                if let Err(error) = state.load_task_store(waiting_task, store) {
                                    return ASTStreamStep::Throw(error);
                                }
                                state.current_task = Some(waiting_task);
                            } else {
                                *store = waiting_store.clone();
                            }
                            if items.len() > 1 {
                                state.merge_gather_logs(&items, store);
                            }
                            resume_to_continuation(k_user, value)
                        }
                        Err(error) => {
                            state.clear_waiters_for_continuation(waiting_cont_id);

                            if let Some(waiting_task) = waiting_task {
                                if let Err(error) = state.load_task_store(waiting_task, store) {
                                    return ASTStreamStep::Throw(error);
                                }
                                state.current_task = Some(waiting_task);
                            } else {
                                *store = waiting_store.clone();
                            }
                            throw_to_continuation(k_user, error)
                        }
                    };
                }
            }
            WaitMode::Any => {
                if let Some(first) = state.collect_any_result(&items) {
                    return match first {
                        Ok(value) => {
                            state.clear_waiters_for_continuation(waiting_cont_id);

                            if let Some(waiting_task) = waiting_task {
                                if let Err(error) = state.load_task_store(waiting_task, store) {
                                    return ASTStreamStep::Throw(error);
                                }
                                state.current_task = Some(waiting_task);
                            } else {
                                *store = waiting_store.clone();
                            }
                            resume_to_continuation(k_user, value)
                        }
                        Err(error) => {
                            state.clear_waiters_for_continuation(waiting_cont_id);

                            if let Some(waiting_task) = waiting_task {
                                if let Err(error) = state.load_task_store(waiting_task, store) {
                                    return ASTStreamStep::Throw(error);
                                }
                                state.current_task = Some(waiting_task);
                            } else {
                                *store = waiting_store.clone();
                            }
                            throw_to_continuation(k_user, error)
                        }
                    };
                }
            }
        }

        // Re-register waiter using the original suspended owner context, not
        // the just-finished running task context.
        state.current_task = waiting_task;
        *store = waiting_store.clone();

        match mode {
            WaitMode::All => state.wait_on_all(&items, k_user.clone(), store),
            WaitMode::Any => state.wait_on_any(&items, k_user.clone(), store),
        }

        let step = state.transfer_next_or(k_user.clone(), store);
        let next_running_task = state.current_task;
        let resumed_waiting_owner = step_targets_continuation(&step, &k_user);
        if next_running_task.is_some() && !resumed_waiting_owner {
            self.phase = SchedulerPhase::Driving {
                k_user,
                items,
                mode,
                running_task: next_running_task,
                waiting_task,
                waiting_store,
            };
        } else {
            self.phase = SchedulerPhase::Idle;
        }
        step
    }
}

impl ASTStreamProgram for SchedulerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k_user: Continuation,
        store: &mut RustStore,
    ) -> ASTStreamStep {
        {
            let mut state = self.state.lock().expect("Scheduler lock poisoned");
            state.process_semaphore_drop_notifications();
            if state.apply_running_task_cancellation_if_requested() {
                return state.transfer_next_or(k_user, store);
            }
        }

        let sched_effect = if let Some(obj) = dispatch_into_python(effect.clone()) {
            match parse_scheduler_python_effect(&obj, None) {
                Ok(Some(se)) => se,
                Ok(None) => {
                    return ASTStreamStep::Yield(DoCtrl::Delegate {
                        effect: dispatch_from_shared(obj),
                    })
                }
                Err(msg) => {
                    return ASTStreamStep::Throw(PyException::type_error(format!(
                        "failed to parse scheduler effect: {msg}"
                    )))
                }
            }
        } else {
            #[cfg(test)]
            {
                return ASTStreamStep::Yield(DoCtrl::Delegate { effect });
            }
            #[cfg(not(test))]
            {
                unreachable!("runtime Effect is always Python")
            }
        };

        match sched_effect {
            SchedulerEffect::Spawn { .. } => {
                self.phase = SchedulerPhase::SpawnAwaitTraceback {
                    k_user: k_user.clone(),
                    effect,
                };
                ASTStreamStep::Yield(DoCtrl::GetTraceback {
                    continuation: k_user,
                })
            }

            SchedulerEffect::CancelTask { task } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                state.request_task_cancellation(task);
                resume_to_continuation(k_user, Value::Unit)
            }

            SchedulerEffect::TaskCompleted { task, result } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                if let Err(error) = state.save_task_store(task, store) {
                    return ASTStreamStep::Throw(error);
                }
                if let Err(error) = state.mark_task_done(task, result) {
                    return ASTStreamStep::Throw(error);
                }
                state.wake_waiters(Waitable::Task(task));
                state.transfer_next_or(k_user, store)
            }

            SchedulerEffect::Gather { items } => self.handle_gather(k_user, items, store),

            SchedulerEffect::Race { items } => self.handle_race(k_user, items, store),

            SchedulerEffect::CreatePromise => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                let pid = state.alloc_promise_id();
                state.promises.insert(pid, PromiseState::Pending);
                resume_to_continuation(k_user, Value::Promise(PromiseHandle { id: pid }))
            }

            SchedulerEffect::CompletePromise { promise, value } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                state.mark_promise_done(promise, Ok(value));
                state.transfer_next_or(k_user, store)
            }

            SchedulerEffect::FailPromise { promise, error } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                state.mark_promise_done(promise, Err(error));
                state.transfer_next_or(k_user, store)
            }

            SchedulerEffect::CreateExternalPromise => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                let pid = state.alloc_promise_id();
                state.promises.insert(pid, PromiseState::Pending);
                let completion_queue = match state.ensure_external_completion_queue() {
                    Ok(queue) => queue,
                    Err(error) => return ASTStreamStep::Throw(error),
                };
                resume_to_continuation(
                    k_user,
                    Value::ExternalPromise(ExternalPromise {
                        id: pid,
                        completion_queue: Some(completion_queue),
                    }),
                )
            }

            SchedulerEffect::CreateSemaphore { permits } => {
                let (semaphore_id, scheduler_state_id) = {
                    let mut state = self.state.lock().expect("Scheduler lock poisoned");
                    let semaphore_id = state.create_semaphore(permits);
                    (semaphore_id, state.state_id())
                };
                let semaphore_value =
                    match make_python_semaphore_value(semaphore_id, scheduler_state_id) {
                        Ok(value) => value,
                        Err(error) => return ASTStreamStep::Throw(error),
                    };
                resume_to_continuation(k_user, semaphore_value)
            }

            SchedulerEffect::AcquireSemaphore { semaphore_id } => {
                let waiter_promise = {
                    let mut state = self.state.lock().expect("Scheduler lock poisoned");
                    match state.acquire_semaphore(semaphore_id) {
                        Ok(result) => result,
                        Err(error) => return ASTStreamStep::Throw(error),
                    }
                };
                match waiter_promise {
                    Some(promise_id) => {
                        let mut state = self.state.lock().expect("Scheduler lock poisoned");
                        let items = [Waitable::Promise(promise_id)];
                        state.wait_on_any(&items, k_user.clone(), store);
                        state.transfer_next_or(k_user, store)
                    }
                    None => resume_to_continuation(k_user, Value::Unit),
                }
            }

            SchedulerEffect::ReleaseSemaphore { semaphore_id } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                if let Err(error) = state.release_semaphore(semaphore_id) {
                    return ASTStreamStep::Throw(error);
                }
                resume_to_continuation(k_user, Value::Unit)
            }

            SchedulerEffect::GetExecutionContext => self.handle_get_execution_context(k_user),
        }
    }

    fn resume(&mut self, value: Value, store: &mut RustStore) -> ASTStreamStep {
        match std::mem::replace(&mut self.phase, SchedulerPhase::Idle) {
            SchedulerPhase::SpawnAwaitTraceback { k_user, effect } => {
                let traceback = match value {
                    Value::Traceback(hops) => hops,
                    _ => {
                        return ASTStreamStep::Throw(PyException::type_error(
                            "scheduler Spawn expected GetTraceback result".to_string(),
                        ));
                    }
                };
                let spawn_site = spawn_site_from_traceback(&traceback);

                let sched_effect = if let Some(obj) = dispatch_into_python(effect.clone()) {
                    match parse_scheduler_python_effect(&obj, spawn_site.clone()) {
                        Ok(Some(se)) => se,
                        Ok(None) => {
                            return ASTStreamStep::Throw(PyException::runtime_error(
                                "scheduler Spawn traceback phase got non-scheduler effect"
                                    .to_string(),
                            ));
                        }
                        Err(msg) => {
                            return ASTStreamStep::Throw(PyException::type_error(format!(
                                "failed to parse scheduler effect: {msg}"
                            )));
                        }
                    }
                } else {
                    #[cfg(test)]
                    {
                        return ASTStreamStep::Throw(PyException::runtime_error(
                            "scheduler Spawn traceback phase requires python effect".to_string(),
                        ));
                    }
                    #[cfg(not(test))]
                    {
                        unreachable!("runtime Effect is always Python")
                    }
                };

                let SchedulerEffect::Spawn {
                    program,
                    handlers,
                    store_mode,
                    creation_site,
                } = sched_effect
                else {
                    return ASTStreamStep::Throw(PyException::runtime_error(
                        "scheduler Spawn traceback phase expected Spawn effect".to_string(),
                    ));
                };

                let store_snapshot = match store_mode {
                    StoreMode::Shared => None,
                    StoreMode::Isolated { .. } => Some(store.clone()),
                };

                if handlers.is_empty() {
                    self.phase = SchedulerPhase::SpawnAwaitHandlers {
                        k_user,
                        program,
                        store_mode,
                        store_snapshot,
                        spawn_site: creation_site,
                    };
                    return ASTStreamStep::Yield(DoCtrl::GetHandlers);
                }

                self.phase = SchedulerPhase::SpawnAwaitContinuation {
                    k_user,
                    store_mode,
                    store_snapshot,
                    spawn_site: creation_site,
                };

                ASTStreamStep::Yield(DoCtrl::CreateContinuation {
                    expr: PyShared::new(program),
                    handlers,
                    handler_identities: vec![],
                })
            }

            SchedulerPhase::SpawnAwaitHandlers {
                k_user,
                program,
                store_mode,
                store_snapshot,
                spawn_site,
            } => {
                let handlers = match value {
                    Value::Handlers(hs) => hs,
                    _ => {
                        return ASTStreamStep::Throw(PyException::type_error(
                            "scheduler Spawn expected GetHandlers result".to_string(),
                        ));
                    }
                };

                self.phase = SchedulerPhase::SpawnAwaitContinuation {
                    k_user,
                    store_mode,
                    store_snapshot,
                    spawn_site,
                };

                ASTStreamStep::Yield(DoCtrl::CreateContinuation {
                    expr: PyShared::new(program),
                    handlers,
                    handler_identities: vec![],
                })
            }

            SchedulerPhase::SpawnAwaitContinuation {
                k_user,
                store_mode,
                store_snapshot,
                spawn_site,
            } => {
                // Value should be the continuation created by CreateContinuation
                let cont = match value {
                    Value::Continuation(c) => c,
                    _ => {
                        return ASTStreamStep::Throw(PyException::type_error(
                            "expected continuation from CreateContinuation, got unexpected type"
                                .to_string(),
                        ));
                    }
                };

                let task_store = match store_mode {
                    StoreMode::Shared => TaskStore::Shared,
                    StoreMode::Isolated { merge } => match store_snapshot {
                        Some(snapshot) => TaskStore::Isolated {
                            store: snapshot,
                            merge,
                        },
                        None => {
                            return ASTStreamStep::Throw(PyException::runtime_error(
                                "isolated spawn missing store snapshot".to_string(),
                            ))
                        }
                    },
                };

                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                let task_id = state.alloc_task_id();
                let parent_task = state.current_task;
                state.tasks.insert(
                    task_id,
                    TaskState::Pending {
                        cont,
                        store: task_store,
                    },
                );
                state.task_metadata.insert(
                    task_id,
                    TaskMetadata {
                        parent_task,
                        spawn_site,
                        spawn_dispatch_id: k_user.dispatch_id,
                    },
                );
                state.ready.push_back(task_id);

                // Transfer back to caller with the task handle
                resume_to_continuation(k_user, Value::Task(TaskHandle { id: task_id }))
            }

            SchedulerPhase::Driving {
                k_user,
                items,
                mode,
                running_task,
                waiting_task,
                waiting_store,
            } => self.continue_driving(
                Ok(value),
                k_user,
                items,
                mode,
                running_task,
                waiting_task,
                waiting_store,
                store,
            ),

            SchedulerPhase::Idle => {
                // Unexpected resume
                ASTStreamStep::Throw(PyException::runtime_error(
                    "Unexpected resume in scheduler: no pending operation".to_string(),
                ))
            }
        }
    }

    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> ASTStreamStep {
        match std::mem::replace(&mut self.phase, SchedulerPhase::Idle) {
            SchedulerPhase::Driving {
                k_user,
                items,
                mode,
                running_task,
                waiting_task,
                waiting_store,
            } => self.continue_driving(
                Err(exc),
                k_user,
                items,
                mode,
                running_task,
                waiting_task,
                waiting_store,
                store,
            ),
            _ => ASTStreamStep::Throw(exc),
        }
    }
}

impl ASTStream for SchedulerProgram {
    fn resume(&mut self, value: Value, store: &mut RustStore) -> ASTStreamStep {
        <Self as ASTStreamProgram>::resume(self, value, store)
    }

    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> ASTStreamStep {
        <Self as ASTStreamProgram>::throw(self, exc, store)
    }

    fn debug_location(&self) -> Option<StreamLocation> {
        Some(StreamLocation {
            function_name: SCHEDULER_HANDLER_NAME.to_string(),
            source_file: "<rust>".to_string(),
            source_line: 0,
            phase: Some(self.current_phase_name().to_string()),
        })
    }
}

// ---------------------------------------------------------------------------
// SchedulerHandler + ASTStreamFactory impl
// ---------------------------------------------------------------------------

#[derive(Clone)]
pub struct SchedulerHandler {
    default_state: Arc<Mutex<SchedulerState>>,
    run_states: Arc<Mutex<HashMap<u64, Arc<Mutex<SchedulerState>>>>>,
}

impl std::fmt::Debug for SchedulerHandler {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SchedulerHandler").finish()
    }
}

impl SchedulerHandler {
    pub fn new() -> Self {
        let default_state = Arc::new(Mutex::new(SchedulerState::new()));
        register_scheduler_state(&default_state);
        SchedulerHandler {
            default_state,
            run_states: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    fn state_for_run(&self, run_token: Option<u64>) -> Arc<Mutex<SchedulerState>> {
        match run_token {
            Some(token) => {
                let mut states = self.run_states.lock().expect("Scheduler lock poisoned");
                states
                    .entry(token)
                    .or_insert_with(|| {
                        let state = Arc::new(Mutex::new(SchedulerState::new()));
                        register_scheduler_state(&state);
                        state
                    })
                    .clone()
            }
            None => self.default_state.clone(),
        }
    }
}

impl ASTStreamFactory for SchedulerHandler {
    fn can_handle(&self, effect: &DispatchEffect) -> Result<bool, VMError> {
        let Some(obj) = dispatch_ref_as_python(effect) else {
            return Ok(false);
        };

        match parse_scheduler_python_effect(obj, None) {
            Ok(Some(_)) | Err(_) => Ok(true),
            Ok(None) => Ok(false),
        }
    }

    fn create_program(&self) -> ASTStreamProgramRef {
        Arc::new(Mutex::new(Box::new(SchedulerProgram::new(
            self.state_for_run(None),
        ))))
    }

    fn create_program_for_run(&self, run_token: Option<u64>) -> ASTStreamProgramRef {
        Arc::new(Mutex::new(Box::new(SchedulerProgram::new(
            self.state_for_run(run_token),
        ))))
    }

    fn handler_name(&self) -> &'static str {
        SCHEDULER_HANDLER_NAME
    }

    fn on_run_end(&self, run_token: u64) {
        let mut states = self.run_states.lock().expect("Scheduler lock poisoned");
        states.remove(&run_token);
    }
}

impl HandlerInvoke for SchedulerHandler {
    fn can_handle(&self, effect: &DispatchEffect) -> Result<bool, VMError> {
        <Self as ASTStreamFactory>::can_handle(self, effect)
    }

    fn invoke(&self, effect: DispatchEffect, k: Continuation) -> DoCtrl {
        DoCtrl::Expand {
            factory: CallArg::Value(Value::RustProgramInvocation(RustProgramInvocation {
                factory: Arc::new(self.clone()),
                effect: Box::new(effect),
                continuation: k,
            })),
            args: vec![],
            kwargs: vec![],
            metadata: CallMetadata::new(
                <Self as ASTStreamFactory>::handler_name(self).to_string(),
                "<rust>".to_string(),
                0,
                None,
                None,
            ),
        }
    }

    fn handler_name(&self) -> &str {
        <Self as ASTStreamFactory>::handler_name(self)
    }

    fn handler_debug_info(&self) -> crate::handler::HandlerDebugInfo {
        crate::handler::HandlerDebugInfo {
            name: <Self as ASTStreamFactory>::handler_name(self).to_string(),
            file: None,
            line: None,
        }
    }

    fn supports_error_context_conversion(&self) -> bool {
        true
    }

    fn on_run_end(&self, run_token: u64) {
        <Self as ASTStreamFactory>::on_run_end(self, run_token);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ast_stream::{ASTStream, ASTStreamStep};
    use crate::capture::TraceFrame;
    use pyo3::{IntoPyObject, Python};

    fn make_test_continuation() -> Continuation {
        use crate::ids::{Marker, SegmentId};
        use crate::segment::Segment;

        let marker = Marker::fresh();
        let seg = Segment::new(marker, None, vec![marker]);
        let seg_id = SegmentId::from_index(0);
        Continuation::capture(&seg, seg_id, None)
    }

    fn make_unstarted_test_continuation() -> Continuation {
        let mut cont = make_test_continuation();
        cont.started = false;
        cont
    }

    #[test]
    fn test_transfer_to_continuation_started_emits_transfer() {
        let cont = make_test_continuation();
        let cont_id = cont.cont_id;
        let step = transfer_to_continuation(cont, Value::Int(123));

        match step {
            ASTStreamStep::Yield(DoCtrl::Transfer {
                continuation,
                value,
            }) => {
                assert_eq!(continuation.cont_id, cont_id);
                assert_eq!(value.as_int(), Some(123));
            }
            _ => panic!("started continuation must emit DoCtrl::Transfer"),
        }
    }

    #[test]
    fn test_transfer_to_continuation_unstarted_emits_resume_continuation() {
        let cont = make_unstarted_test_continuation();
        let cont_id = cont.cont_id;
        let step = transfer_to_continuation(cont, Value::Int(456));

        match step {
            ASTStreamStep::Yield(DoCtrl::ResumeContinuation {
                continuation,
                value,
            }) => {
                assert_eq!(continuation.cont_id, cont_id);
                assert_eq!(value.as_int(), Some(456));
            }
            _ => panic!("unstarted continuation must emit DoCtrl::ResumeContinuation"),
        }
    }

    #[test]
    fn test_scheduler_ast_stream_spawn_sequence_and_debug_location() {
        Python::attach(|py| {
            let state = Arc::new(Mutex::new(SchedulerState::new()));
            let mut program = SchedulerProgram::new(state);
            let mut store = RustStore::new();
            let k_user = make_test_continuation();
            let k_user_id = k_user.cont_id;
            let spawn_program = py.None().into_pyobject(py).unwrap().unbind().into_any();
            let spawn_effect = Py::new(
                py,
                PySpawn::create(py, spawn_program.clone_ref(py), None, None, None),
            )
            .expect("failed to create SpawnEffect")
            .into_any();

            program.phase = SchedulerPhase::SpawnAwaitTraceback {
                k_user,
                effect: dispatch_from_shared(PyShared::new(spawn_effect)),
            };

            let location = ASTStream::debug_location(&program).expect("scheduler debug location");
            assert_eq!(location.function_name, SCHEDULER_HANDLER_NAME);
            assert_eq!(location.phase.as_deref(), Some("SpawnAwaitTraceback"));

            let traceback = vec![TraceHop {
                frames: vec![TraceFrame {
                    func_name: "parent".to_string(),
                    source_file: "/tmp/user_program.py".to_string(),
                    source_line: 321,
                }],
            }];
            let step = ASTStream::resume(&mut program, Value::Traceback(traceback), &mut store);
            assert!(matches!(step, ASTStreamStep::Yield(DoCtrl::GetHandlers)));

            let location = ASTStream::debug_location(&program).expect("scheduler debug location");
            assert_eq!(location.phase.as_deref(), Some("SpawnAwaitHandlers"));

            let step = ASTStream::resume(&mut program, Value::Handlers(vec![]), &mut store);
            assert!(matches!(
                step,
                ASTStreamStep::Yield(DoCtrl::CreateContinuation { .. })
            ));

            let location = ASTStream::debug_location(&program).expect("scheduler debug location");
            assert_eq!(location.phase.as_deref(), Some("SpawnAwaitContinuation"));

            let created_continuation = make_test_continuation();
            let step = ASTStream::resume(
                &mut program,
                Value::Continuation(created_continuation),
                &mut store,
            );

            match step {
                ASTStreamStep::Yield(DoCtrl::Resume {
                    continuation,
                    value,
                })
                | ASTStreamStep::Yield(DoCtrl::Transfer {
                    continuation,
                    value,
                })
                | ASTStreamStep::Yield(DoCtrl::ResumeContinuation {
                    continuation,
                    value,
                }) => {
                    assert_eq!(continuation.cont_id, k_user_id);
                    assert!(matches!(value, Value::Task(_)));
                }
                _ => panic!("expected ASTStream Yield(Resume|Transfer|ResumeContinuation)"),
            }

            let location = ASTStream::debug_location(&program).expect("scheduler debug location");
            assert_eq!(location.phase.as_deref(), Some("Idle"));
        });
    }

    #[test]
    fn test_spawn_site_from_traceback_uses_outermost_user_hop() {
        let hops = vec![
            TraceHop {
                frames: vec![TraceFrame {
                    func_name: "delegate_b".to_string(),
                    source_file: "/tmp/handlers.py".to_string(),
                    source_line: 30,
                }],
            },
            TraceHop {
                frames: vec![TraceFrame {
                    func_name: "delegate_a".to_string(),
                    source_file: "/tmp/handlers.py".to_string(),
                    source_line: 20,
                }],
            },
            TraceHop {
                frames: vec![
                    TraceFrame {
                        func_name: "parent".to_string(),
                        source_file: "/tmp/user_program.py".to_string(),
                        source_line: 88,
                    },
                    TraceFrame {
                        func_name: "_spawn_task".to_string(),
                        source_file: "/repo/doeff/effects/spawn.py".to_string(),
                        source_line: 199,
                    },
                ],
            },
        ];

        let site = spawn_site_from_traceback(&hops)
            .expect("traceback should produce spawn site candidate");
        assert_eq!(site.function_name, "parent");
        assert_eq!(site.source_file, "/tmp/user_program.py");
        assert_eq!(site.source_line, 88);
    }

    #[test]
    fn test_scheduler_task_switch_no_segment_growth() {
        let mut state = SchedulerState::new();
        let mut store = RustStore::new();
        let scheduler_k = make_test_continuation();

        let task0 = state.alloc_task_id();
        let task1 = state.alloc_task_id();

        let cont0 = make_test_continuation();
        let cont1 = make_test_continuation();
        state.tasks.insert(
            task0,
            TaskState::Pending {
                cont: cont0.clone(),
                store: TaskStore::Shared,
            },
        );
        state.tasks.insert(
            task1,
            TaskState::Pending {
                cont: cont1.clone(),
                store: TaskStore::Shared,
            },
        );

        for i in 0..128 {
            let (task, expected_cont) = if i % 2 == 0 {
                (task0, cont0.cont_id)
            } else {
                (task1, cont1.cont_id)
            };
            state.ready.push_back(task);

            let step = state.transfer_next_or(scheduler_k.clone(), &mut store);
            match step {
                ASTStreamStep::Yield(DoCtrl::Transfer { continuation, .. }) => {
                    assert_eq!(continuation.cont_id, expected_cont);
                }
                ASTStreamStep::Yield(DoCtrl::Resume { .. }) => {
                    panic!("task switches must not emit DoCtrl::Resume")
                }
                _ => panic!("task switches must emit DoCtrl::Transfer"),
            }

            // Simulate that the resumed task yielded back to scheduler.
            state.current_task = None;
        }
    }

    #[test]
    fn test_scheduler_task_completion_routes_via_envelope() {
        let mut state = SchedulerState::new();
        let mut store = RustStore::new();

        let task_id = state.alloc_task_id();
        state.tasks.insert(
            task_id,
            TaskState::Pending {
                cont: make_test_continuation(),
                store: TaskStore::Shared,
            },
        );

        let waiter = make_test_continuation();
        state.wait_on_all(&[Waitable::Task(task_id)], waiter.clone(), &store);
        state
            .mark_task_done(task_id, Ok(Value::Int(7)))
            .expect("task should exist when marking done");
        state.wake_waiters(Waitable::Task(task_id));

        // transfer_next_or only resumes waiters that belong to the same owner continuation.
        let step = state.transfer_next_or(waiter.clone(), &mut store);
        match step {
            ASTStreamStep::Yield(DoCtrl::Resume {
                continuation,
                value,
            }) => {
                assert_eq!(continuation.cont_id, waiter.cont_id);
                match value {
                    Value::List(values) => {
                        assert_eq!(values.len(), 1);
                        assert_eq!(values[0].as_int(), Some(7));
                    }
                    _ => panic!("wait completion should resume waiter with gathered value"),
                }
            }
            _ => panic!("completed waiter must resume via DoCtrl::Resume"),
        }
    }

    #[test]
    fn test_store_mode_shared() {
        let mode = StoreMode::Shared;
        assert!(matches!(mode, StoreMode::Shared));
    }

    #[test]
    fn test_store_mode_isolated() {
        let mode = StoreMode::Isolated {
            merge: StoreMergePolicy::LogsOnly,
        };
        assert!(matches!(mode, StoreMode::Isolated { .. }));
    }

    #[test]
    fn test_waitable_equality() {
        let w1 = Waitable::Task(TaskId::from_raw(1));
        let w2 = Waitable::Task(TaskId::from_raw(1));
        let w3 = Waitable::Promise(PromiseId::from_raw(1));
        assert_eq!(w1, w2);
        assert_ne!(w1, w3);
    }

    #[test]
    fn test_parse_spawn_effect_uses_passed_creation_site() {
        Python::attach(|py| {
            let spawn = Py::new(py, PySpawn::create(py, py.None(), None, None, None))
                .expect("failed to create SpawnEffect");
            let obj = spawn.into_any();

            let creation_site = Some(SpawnSite {
                function_name: "parent".to_string(),
                source_file: "/tmp/user_program.py".to_string(),
                source_line: 321,
            });

            let parsed = parse_scheduler_python_effect(&PyShared::new(obj), creation_site)
                .expect("failed to parse effect")
                .expect("effect should be parsed as scheduler spawn");
            match parsed {
                SchedulerEffect::Spawn { creation_site, .. } => {
                    let site = creation_site.expect("spawn creation site should be captured");
                    assert_eq!(site.function_name, "parent");
                    assert_eq!(site.source_file, "/tmp/user_program.py");
                    assert_eq!(site.source_line, 321);
                }
                _ => panic!("expected SchedulerEffect::Spawn"),
            }
        });
    }

    #[test]
    fn test_task_handle_clone() {
        let handle = TaskHandle {
            id: TaskId::from_raw(42),
        };
        let cloned = handle;
        assert_eq!(handle.id, cloned.id);
    }

    #[test]
    fn test_promise_state_variants() {
        let pending = PromiseState::Pending;
        assert!(matches!(pending, PromiseState::Pending));

        let done = PromiseState::Done(Ok(Value::Int(42)));
        assert!(matches!(done, PromiseState::Done(Ok(Value::Int(42)))));
    }

    #[test]
    fn test_scheduler_state_new() {
        let state = SchedulerState::new();
        assert!(state.ready.is_empty());
        assert!(state.tasks.is_empty());
        assert!(state.promises.is_empty());
        assert_eq!(state.next_task, 0);
        assert_eq!(state.next_promise, 0);
        assert!(state.current_task.is_none());
    }

    #[test]
    fn test_scheduler_state_alloc_ids() {
        let mut state = SchedulerState::new();
        let t1 = state.alloc_task_id();
        let t2 = state.alloc_task_id();
        assert_ne!(t1, t2);
        assert_eq!(t1, TaskId::from_raw(0));
        assert_eq!(t2, TaskId::from_raw(1));

        let p1 = state.alloc_promise_id();
        let p2 = state.alloc_promise_id();
        assert_ne!(p1, p2);
        assert_eq!(p1, PromiseId::from_raw(0));
        assert_eq!(p2, PromiseId::from_raw(1));
    }

    #[test]
    fn test_scheduler_state_promise_lifecycle() {
        let mut state = SchedulerState::new();
        let pid = state.alloc_promise_id();
        state.promises.insert(pid, PromiseState::Pending);
        assert!(matches!(
            state.promises.get(&pid),
            Some(PromiseState::Pending)
        ));

        state
            .promises
            .insert(pid, PromiseState::Done(Ok(Value::Int(42))));
        assert!(matches!(
            state.promises.get(&pid),
            Some(PromiseState::Done(Ok(Value::Int(42))))
        ));
    }

    #[test]
    fn test_scheduler_state_try_race_none_done() {
        let state = SchedulerState::new();
        let result = state.try_race(&[Waitable::Task(TaskId::from_raw(0))]);
        assert!(result.is_none());
    }

    #[test]
    fn test_scheduler_handler_can_handle() {
        let handler = SchedulerHandler::new();
        assert!(!HandlerInvoke::can_handle(
            &handler,
            &Effect::Get {
                key: "x".to_string()
            }
        )
        .unwrap());
    }

    #[test]
    fn test_waitable_external_promise() {
        let pid = PromiseId::from_raw(5);
        let w = Waitable::ExternalPromise(pid);
        assert!(matches!(w, Waitable::ExternalPromise(id) if id == pid));
        assert_ne!(w, Waitable::Promise(pid));
    }

    #[test]
    fn test_external_promise_try_race() {
        let mut state = SchedulerState::new();
        let pid = state.alloc_promise_id();
        state
            .promises
            .insert(pid, PromiseState::Done(Ok(Value::Int(99))));

        let result = state.try_race(&[Waitable::ExternalPromise(pid)]);
        assert!(result.is_some());
        assert_eq!(result.unwrap().as_int(), Some(99));
    }

    #[test]
    fn test_external_promise_try_collect() {
        let mut state = SchedulerState::new();
        let pid = state.alloc_promise_id();
        state
            .promises
            .insert(pid, PromiseState::Done(Ok(Value::Int(77))));

        let result = state.try_collect(&[Waitable::ExternalPromise(pid)]);
        assert!(result.is_some());
        match result.unwrap() {
            Value::List(values) => {
                assert_eq!(values.len(), 1);
                assert_eq!(values[0].as_int(), Some(77));
            }
            other => panic!("Expected Value::List, got {:?}", other),
        }
    }

    #[test]
    fn test_remove_semaphore_drops_runtime_state() {
        let mut state = SchedulerState::new();
        let semaphore_id = state.create_semaphore(1);

        assert!(state.semaphores.contains_key(&semaphore_id));
        state.remove_semaphore(semaphore_id);
        assert!(!state.semaphores.contains_key(&semaphore_id));
    }

    #[test]
    fn test_remove_semaphore_cancels_pending_waiters() {
        let mut state = SchedulerState::new();
        let semaphore_id = state.create_semaphore(1);
        let holder = TaskId::from_raw(1);
        let waiter_task = TaskId::from_raw(2);

        state.current_task = Some(holder);
        let immediate = state.acquire_semaphore(semaphore_id).unwrap();
        assert!(immediate.is_none());

        state.current_task = Some(waiter_task);
        let waiter_promise = state
            .acquire_semaphore(semaphore_id)
            .unwrap()
            .expect("second acquire should block");
        assert!(matches!(
            state.promises.get(&waiter_promise),
            Some(PromiseState::Pending)
        ));

        state.remove_semaphore(semaphore_id);
        assert!(!state.semaphores.contains_key(&semaphore_id));
        assert!(matches!(
            state.promises.get(&waiter_promise),
            Some(PromiseState::Done(Err(_)))
        ));
    }

    #[test]
    fn test_semaphore_drop_notification_is_drained_for_state() {
        let mut state = SchedulerState::new();
        let semaphore_id = state.create_semaphore(1);

        notify_semaphore_handle_dropped(state.state_id(), semaphore_id);
        state.process_semaphore_drop_notifications();

        assert!(!state.semaphores.contains_key(&semaphore_id));
    }

    // -----------------------------------------------------------------------
    // ISSUE-VM-003: Gather collects results from multiple tasks/promises
    // -----------------------------------------------------------------------

    #[test]
    fn test_gather_collects_multiple_task_results() {
        let mut state = SchedulerState::new();

        // Create 3 tasks, all done with known values
        let t0 = state.alloc_task_id();
        let t1 = state.alloc_task_id();
        let t2 = state.alloc_task_id();

        state.tasks.insert(
            t0,
            TaskState::Done {
                result: Ok(Value::Int(10)),
                store: TaskStore::Shared,
            },
        );
        state.tasks.insert(
            t1,
            TaskState::Done {
                result: Ok(Value::Int(20)),
                store: TaskStore::Shared,
            },
        );
        state.tasks.insert(
            t2,
            TaskState::Done {
                result: Ok(Value::Int(30)),
                store: TaskStore::Shared,
            },
        );

        let items = vec![Waitable::Task(t0), Waitable::Task(t1), Waitable::Task(t2)];
        let result = state.try_collect(&items);
        assert!(
            result.is_some(),
            "try_collect should succeed when all tasks are done"
        );
        match result.unwrap() {
            Value::List(values) => {
                assert_eq!(values.len(), 3);
                assert_eq!(values[0].as_int(), Some(10));
                assert_eq!(values[1].as_int(), Some(20));
                assert_eq!(values[2].as_int(), Some(30));
            }
            other => panic!(
                "Expected Value::List for multi-item gather, got {:?}",
                other
            ),
        }
    }

    #[test]
    fn test_gather_returns_none_when_any_task_pending() {
        let mut state = SchedulerState::new();
        let t0 = state.alloc_task_id();
        let t1 = state.alloc_task_id();

        state.tasks.insert(
            t0,
            TaskState::Done {
                result: Ok(Value::Int(10)),
                store: TaskStore::Shared,
            },
        );
        // t1 is Pending — gather should return None
        let cont = make_test_continuation();
        state.tasks.insert(
            t1,
            TaskState::Pending {
                cont,
                store: TaskStore::Shared,
            },
        );

        let result = state.try_collect(&[Waitable::Task(t0), Waitable::Task(t1)]);
        assert!(
            result.is_none(),
            "try_collect should return None when any task is still pending"
        );
    }

    #[test]
    fn test_gather_mixed_tasks_and_promises() {
        let mut state = SchedulerState::new();
        let tid = state.alloc_task_id();
        let pid = state.alloc_promise_id();

        state.tasks.insert(
            tid,
            TaskState::Done {
                result: Ok(Value::Int(100)),
                store: TaskStore::Shared,
            },
        );
        state
            .promises
            .insert(pid, PromiseState::Done(Ok(Value::Int(200))));

        let items = vec![Waitable::Task(tid), Waitable::Promise(pid)];
        let result = state.try_collect(&items);
        assert!(result.is_some());
        match result.unwrap() {
            Value::List(values) => {
                assert_eq!(values.len(), 2);
                assert_eq!(values[0].as_int(), Some(100));
                assert_eq!(values[1].as_int(), Some(200));
            }
            other => panic!("Expected Value::List, got {:?}", other),
        }
    }

    // -----------------------------------------------------------------------
    // ISSUE-VM-003: Race returns first completed result
    // -----------------------------------------------------------------------

    #[test]
    fn test_race_returns_first_done_task() {
        let mut state = SchedulerState::new();
        let t0 = state.alloc_task_id();
        let t1 = state.alloc_task_id();
        let t2 = state.alloc_task_id();

        // t0 still pending
        let cont = make_test_continuation();
        state.tasks.insert(
            t0,
            TaskState::Pending {
                cont,
                store: TaskStore::Shared,
            },
        );
        // t1 is done — should be returned by race
        state.tasks.insert(
            t1,
            TaskState::Done {
                result: Ok(Value::Int(42)),
                store: TaskStore::Shared,
            },
        );
        // t2 also done but comes after t1 in iteration order
        state.tasks.insert(
            t2,
            TaskState::Done {
                result: Ok(Value::Int(99)),
                store: TaskStore::Shared,
            },
        );

        let items = vec![Waitable::Task(t0), Waitable::Task(t1), Waitable::Task(t2)];
        let result = state.try_race(&items);
        assert!(
            result.is_some(),
            "try_race should succeed when any task is done"
        );
        // Returns the first done in iteration order (t1)
        assert_eq!(result.unwrap().as_int(), Some(42));
    }

    #[test]
    fn test_race_returns_none_when_all_pending() {
        let mut state = SchedulerState::new();
        let t0 = state.alloc_task_id();
        let t1 = state.alloc_task_id();

        let cont0 = make_test_continuation();
        let cont1 = make_test_continuation();
        state.tasks.insert(
            t0,
            TaskState::Pending {
                cont: cont0,
                store: TaskStore::Shared,
            },
        );
        state.tasks.insert(
            t1,
            TaskState::Pending {
                cont: cont1,
                store: TaskStore::Shared,
            },
        );

        let result = state.try_race(&[Waitable::Task(t0), Waitable::Task(t1)]);
        assert!(
            result.is_none(),
            "try_race should return None when all tasks are pending"
        );
    }

    // -----------------------------------------------------------------------
    // ISSUE-VM-003: Gather/Race handler paths — immediate resolution
    // -----------------------------------------------------------------------

    #[test]
    fn test_scheduler_gather_immediate_when_all_done() {
        // When all tasks are already done, the scheduler handler should
        // Transfer(k_user, collected_results) immediately.
        let sched_state = Arc::new(Mutex::new(SchedulerState::new()));

        // Pre-populate: 2 tasks, both done
        let (t0, t1) = {
            let mut state = sched_state.lock().unwrap();
            let t0 = state.alloc_task_id();
            let t1 = state.alloc_task_id();
            state.tasks.insert(
                t0,
                TaskState::Done {
                    result: Ok(Value::Int(10)),
                    store: TaskStore::Shared,
                },
            );
            state.tasks.insert(
                t1,
                TaskState::Done {
                    result: Ok(Value::Int(20)),
                    store: TaskStore::Shared,
                },
            );
            (t0, t1)
        };

        // Verify the Gather path: try_collect returns immediately when all done
        let state = sched_state.lock().unwrap();
        let items = vec![Waitable::Task(t0), Waitable::Task(t1)];
        let result = state.try_collect(&items);
        assert!(result.is_some());
        match result.unwrap() {
            Value::List(values) => {
                assert_eq!(values.len(), 2);
                assert_eq!(values[0].as_int(), Some(10));
                assert_eq!(values[1].as_int(), Some(20));
            }
            other => panic!("Expected Value::List, got {:?}", other),
        }
    }

    #[test]
    fn test_scheduler_race_immediate_when_first_done() {
        let sched_state = Arc::new(Mutex::new(SchedulerState::new()));

        // Pre-populate: 2 tasks, only t1 done
        let (t0, t1) = {
            let mut state = sched_state.lock().unwrap();
            let t0 = state.alloc_task_id();
            let t1 = state.alloc_task_id();
            let cont = make_test_continuation();
            state.tasks.insert(
                t0,
                TaskState::Pending {
                    cont,
                    store: TaskStore::Shared,
                },
            );
            state.tasks.insert(
                t1,
                TaskState::Done {
                    result: Ok(Value::Int(42)),
                    store: TaskStore::Shared,
                },
            );
            (t0, t1)
        };

        let state = sched_state.lock().unwrap();
        let result = state.try_race(&[Waitable::Task(t0), Waitable::Task(t1)]);
        assert!(result.is_some());
        assert_eq!(result.unwrap().as_int(), Some(42));
    }

    // -----------------------------------------------------------------------
    // ISSUE-VM-003: Waiter wakeup for Gather/Race
    // -----------------------------------------------------------------------

    #[test]
    fn test_gather_waiter_woken_when_last_task_completes() {
        let mut state = SchedulerState::new();
        let t0 = state.alloc_task_id();
        let t1 = state.alloc_task_id();

        // t0 already done
        state.tasks.insert(
            t0,
            TaskState::Done {
                result: Ok(Value::Int(10)),
                store: TaskStore::Shared,
            },
        );
        // t1 still pending
        let cont = make_test_continuation();
        state.tasks.insert(
            t1,
            TaskState::Pending {
                cont,
                store: TaskStore::Shared,
            },
        );

        // Register a waiter on t1 (simulating what wait_on_all does
        // for the one remaining pending item)
        let waiter = make_test_continuation();
        let waiter_id = waiter.cont_id;
        state.wait_on_all(
            &[Waitable::Task(t0), Waitable::Task(t1)],
            waiter,
            &RustStore::new(),
        );

        // Only t1 should have a waiter (t0 is already done)
        assert!(
            state.waiters.contains_key(&Waitable::Task(t1)),
            "waiter should be registered on pending task t1"
        );
        assert!(
            !state.waiters.contains_key(&Waitable::Task(t0)),
            "no waiter should be registered on already-done task t0"
        );

        // Complete t1 and wake
        state
            .mark_task_done(t1, Ok(Value::Int(20)))
            .expect("task should exist when marking done");
        state.wake_waiters(Waitable::Task(t1));

        // Waiter should now be in ready_waiters
        assert_eq!(state.ready_waiters.len(), 1);
        assert_eq!(state.ready_waiters[0].continuation.cont_id, waiter_id);

        // Now try_collect should succeed
        let result = state.try_collect(&[Waitable::Task(t0), Waitable::Task(t1)]);
        assert!(result.is_some());
        match result.unwrap() {
            Value::List(values) => {
                assert_eq!(values.len(), 2);
                assert_eq!(values[0].as_int(), Some(10));
                assert_eq!(values[1].as_int(), Some(20));
            }
            other => panic!("Expected Value::List, got {:?}", other),
        }
    }

    #[test]
    fn test_race_waiter_woken_when_any_task_completes() {
        let mut state = SchedulerState::new();
        let t0 = state.alloc_task_id();
        let t1 = state.alloc_task_id();

        let cont0 = make_test_continuation();
        let cont1 = make_test_continuation();
        state.tasks.insert(
            t0,
            TaskState::Pending {
                cont: cont0,
                store: TaskStore::Shared,
            },
        );
        state.tasks.insert(
            t1,
            TaskState::Pending {
                cont: cont1,
                store: TaskStore::Shared,
            },
        );

        // Register race waiter on both
        let waiter = make_test_continuation();
        let waiter_id = waiter.cont_id;
        state.wait_on_any(
            &[Waitable::Task(t0), Waitable::Task(t1)],
            waiter,
            &RustStore::new(),
        );

        // Complete only t1
        state
            .mark_task_done(t1, Ok(Value::Int(99)))
            .expect("task should exist when marking done");
        state.wake_waiters(Waitable::Task(t1));

        // Waiter should be in ready_waiters
        assert_eq!(state.ready_waiters.len(), 1);
        assert_eq!(state.ready_waiters[0].continuation.cont_id, waiter_id);

        // try_race should return t1's result
        let result = state.try_race(&[Waitable::Task(t0), Waitable::Task(t1)]);
        assert!(result.is_some());
        assert_eq!(result.unwrap().as_int(), Some(99));
    }

    #[test]
    fn test_scheduler_store_save_load() {
        let mut state = SchedulerState::new();
        let tid = state.alloc_task_id();

        let mut store = RustStore::new();
        store.put("key".to_string(), Value::Int(1));

        // Create a task with isolated store
        use crate::ids::Marker;
        use crate::segment::Segment;
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None, vec![]);
        let seg_id = crate::ids::SegmentId::from_index(0);
        let cont = Continuation::capture(&seg, seg_id, None);

        state.tasks.insert(
            tid,
            TaskState::Pending {
                cont,
                store: TaskStore::Isolated {
                    store: store.clone(),
                    merge: StoreMergePolicy::LogsOnly,
                },
            },
        );

        // Save updated store
        store.put("key".to_string(), Value::Int(42));
        state
            .save_task_store(tid, &store)
            .expect("task store save should succeed for existing task");

        // Load back
        let mut loaded_store = RustStore::new();
        state
            .load_task_store(tid, &mut loaded_store)
            .expect("task store load should succeed for existing task");
        assert_eq!(loaded_store.get("key").unwrap().as_int(), Some(42));
    }

    #[test]
    fn test_gather_immediate_clears_stale_ready_waiters_for_same_continuation() {
        let state = Arc::new(Mutex::new(SchedulerState::new()));
        let (t0, t1, k_user) = {
            let mut s = state.lock().unwrap();
            let t0 = s.alloc_task_id();
            let t1 = s.alloc_task_id();
            s.tasks.insert(
                t0,
                TaskState::Done {
                    result: Ok(Value::Int(1)),
                    store: TaskStore::Shared,
                },
            );
            s.tasks.insert(
                t1,
                TaskState::Done {
                    result: Ok(Value::Int(2)),
                    store: TaskStore::Shared,
                },
            );

            let k_user = make_test_continuation();
            s.ready_waiters.push_back(WaitRequest {
                continuation: k_user.clone(),
                items: vec![Waitable::Task(t0), Waitable::Task(t1)],
                mode: WaitMode::All,
                waiting_task: None,
                waiting_store: RustStore::new(),
            });

            (t0, t1, k_user)
        };

        let mut program = SchedulerProgram::new(state.clone());
        let mut store = RustStore::new();
        let step = program.handle_gather(
            k_user.clone(),
            vec![Waitable::Task(t0), Waitable::Task(t1)],
            &mut store,
        );

        assert!(step_targets_continuation(&step, &k_user));

        let s = state.lock().unwrap();
        assert!(
            s.ready_waiters
                .iter()
                .all(|waiter| waiter.continuation.cont_id != k_user.cont_id),
            "stale ready_waiter for already-resumed continuation must be removed"
        );
    }
}
