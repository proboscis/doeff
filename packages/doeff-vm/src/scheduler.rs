//! Scheduler types for cooperative multitasking.
//!
//! The scheduler is a RustProgramHandler that manages tasks, promises,
//! and cooperative scheduling via Transfer-only semantics.

use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};

use pyo3::prelude::*;

use crate::continuation::Continuation;
use crate::effect::{
    DispatchEffect, PyCompletePromise, PyCreateExternalPromise, PyCreatePromise, PyFailPromise,
    PyGather, PyRace, PySpawn, PyTaskCompleted, dispatch_from_shared, dispatch_into_python,
    dispatch_ref_as_python,
};
#[cfg(test)]
use crate::effect::Effect;
use crate::handler::{
    Handler, RustHandlerProgram, RustProgramHandler, RustProgramRef, RustProgramStep,
};
use crate::ids::{PromiseId, TaskId};
use crate::py_shared::PyShared;
use crate::pyvm::PyRustHandlerSentinel;
use crate::step::{DoCtrl, PyException, Yielded};
use crate::value::Value;
use crate::vm::RustStore;

/// Effect variants handled by the scheduler.
#[derive(Debug, Clone)]
pub enum SchedulerEffect {
    Spawn {
        program: Py<PyAny>,
        handlers: Vec<Handler>,
        store_mode: StoreMode,
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
    TaskCompleted {
        task: TaskId,
        result: Result<Value, PyException>,
    },
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
#[derive(Clone, Copy, Debug)]
pub struct ExternalPromise {
    pub id: PromiseId,
}

/// The scheduler's internal state.
pub struct SchedulerState {
    pub ready: VecDeque<TaskId>,
    pub ready_waiters: VecDeque<Continuation>,
    pub tasks: HashMap<TaskId, TaskState>,
    pub promises: HashMap<PromiseId, PromiseState>,
    pub waiters: HashMap<Waitable, Vec<Continuation>>,
    pub next_task: u64,
    pub next_promise: u64,
    pub current_task: Option<TaskId>,
}

fn is_instance_from(obj: &Bound<'_, PyAny>, module: &str, class_name: &str) -> bool {
    let py = obj.py();
    let Ok(mod_) = py.import(module) else {
        return false;
    };
    let Ok(cls) = mod_.getattr(class_name) else {
        return false;
    };
    obj.is_instance(&cls).unwrap_or(false)
}

fn parse_scheduler_python_effect(effect: &PyShared) -> Result<Option<SchedulerEffect>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);

        if let Ok(spawn) = obj.extract::<PyRef<'_, PySpawn>>() {
            let handlers = extract_handlers_from_python(spawn.handlers.bind(py))?;
            let store_mode = parse_store_mode(spawn.store_mode.bind(py))?;
            return Ok(Some(SchedulerEffect::Spawn {
                program: spawn.program.clone_ref(py),
                handlers,
                store_mode,
            }));
        }

        if let Ok(gather) = obj.extract::<PyRef<'_, PyGather>>() {
            let mut waitables = Vec::new();
            for item in gather.items.bind(py).try_iter().map_err(|e| e.to_string())? {
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
            for item in race.futures.bind(py).try_iter().map_err(|e| e.to_string())? {
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
                return Err("FailPromiseEffect.promise must carry _promise_handle.promise_id".to_string());
            };
            let error = pyobject_to_exception(py, fail.error.bind(py));
            return Ok(Some(SchedulerEffect::FailPromise { promise, error }));
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

            let error_obj = done.error.bind(py);
            if !error_obj.is_none() {
                let error = pyobject_to_exception(py, error_obj);
                return Ok(Some(SchedulerEffect::TaskCompleted {
                    task,
                    result: Err(error),
                }));
            }

            let result_obj = done.result.bind(py);
            if !result_obj.is_none() {
                return Ok(Some(SchedulerEffect::TaskCompleted {
                    task,
                    result: Ok(Value::from_pyobject(result_obj)),
                }));
            }

            return Err(
                "TaskCompletedEffect/SchedulerTaskCompleted requires task + result or error"
                    .to_string(),
            );
        }

        if is_instance_from(obj, "doeff.effects.spawn", "SpawnEffect") {
                let program = obj.getattr ("program").map_err(|e| e.to_string())?.unbind();
                let handlers = if let Ok(handlers_obj) = obj.getattr ("handlers") {
                    extract_handlers_from_python(&handlers_obj)?
                } else {
                    vec![]
                };
                let store_mode = if let Ok(mode_obj) = obj.getattr ("store_mode") {
                    parse_store_mode(&mode_obj)?
                } else {
                    StoreMode::Shared
                };
                Ok(Some(SchedulerEffect::Spawn {
                    program,
                    handlers,
                    store_mode,
                }))
        } else if is_instance_from(obj, "doeff.effects.gather", "GatherEffect") {
                let items_obj = obj.getattr ("items").map_err(|e| e.to_string())?;
                let mut waitables = Vec::new();
                for item in items_obj.try_iter().map_err(|e| e.to_string())? {
                    let item = item.map_err(|e| e.to_string())?;
                    match extract_waitable(&item) {
                        Some(w) => waitables.push(w),
                        None => {
                            return Err("GatherEffect.items must be waitable handles".to_string());
                        }
                    }
                }
                Ok(Some(SchedulerEffect::Gather { items: waitables }))
        } else if is_instance_from(obj, "doeff.effects.race", "RaceEffect") {
                let items_obj = obj.getattr ("futures").map_err(|e| e.to_string())?;
                let mut waitables = Vec::new();
                for item in items_obj.try_iter().map_err(|e| e.to_string())? {
                    let item = item.map_err(|e| e.to_string())?;
                    match extract_waitable(&item) {
                        Some(w) => waitables.push(w),
                        None => {
                            return Err("RaceEffect.futures/items must be waitable handles".to_string());
                        }
                    }
                }
                Ok(Some(SchedulerEffect::Race { items: waitables }))
        } else if is_instance_from(obj, "doeff.effects.promise", "CreatePromiseEffect") {
            Ok(Some(SchedulerEffect::CreatePromise))
        } else if is_instance_from(
            obj,
            "doeff.effects.external_promise",
            "CreateExternalPromiseEffect",
        ) {
            Ok(Some(SchedulerEffect::CreateExternalPromise))
        } else if is_instance_from(obj, "doeff.effects.promise", "CompletePromiseEffect") {
                let promise_obj = obj.getattr ("promise").map_err(|e| e.to_string())?;
                let Some(promise) = extract_promise_id(&promise_obj) else {
                    return Err(
                        "CompletePromiseEffect.promise must carry _promise_handle.promise_id"
                            .to_string(),
                    );
                };
                let value = obj.getattr ("value").map_err(|e| e.to_string())?;
                Ok(Some(SchedulerEffect::CompletePromise {
                    promise,
                    value: Value::from_pyobject(&value),
                }))
        } else if is_instance_from(obj, "doeff.effects.promise", "FailPromiseEffect") {
                let promise_obj = obj.getattr ("promise").map_err(|e| e.to_string())?;
                let Some(promise) = extract_promise_id(&promise_obj) else {
                    return Err(
                        "FailPromiseEffect.promise must carry _promise_handle.promise_id".to_string(),
                    );
                };
                let error_obj = obj.getattr ("error").map_err(|e| e.to_string())?;
                let error = pyobject_to_exception(py, &error_obj);
                Ok(Some(SchedulerEffect::FailPromise { promise, error }))
        } else if is_instance_from(obj, "doeff.effects.scheduler_internal", "_SchedulerTaskCompleted") {
                let task = if let Ok(task_obj) = obj.getattr ("task") {
                    extract_task_id(&task_obj)
                } else {
                    None
                }
                .ok_or_else(|| {
                    "TaskCompletedEffect/SchedulerTaskCompleted requires task.task_id"
                        .to_string()
                })?;

                if let Ok(error_obj) = obj.getattr ("error") {
                    let error = pyobject_to_exception(py, &error_obj);
                    return Ok(Some(SchedulerEffect::TaskCompleted {
                        task,
                        result: Err(error),
                    }));
                }
                if let Ok(result_obj) = obj.getattr ("result") {
                    return Ok(Some(SchedulerEffect::TaskCompleted {
                        task,
                        result: Ok(Value::from_pyobject(&result_obj)),
                    }));
                }
                Err(
                    "TaskCompletedEffect/SchedulerTaskCompleted requires task + result or error"
                        .to_string(),
                )
        } else {
            Ok(None)
        }
    })
}

fn extract_waitable(obj: &Bound<'_, PyAny>) -> Option<Waitable> {
    let handle = obj.getattr ("_handle").ok()?;
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
    let handle = obj.getattr ("_promise_handle").ok()?;
    let raw: u64 = handle.get_item("promise_id").ok()?.extract().ok()?;
    Some(PromiseId::from_raw(raw))
}

fn extract_task_id(obj: &Bound<'_, PyAny>) -> Option<TaskId> {
    if let Ok(handle) = obj.getattr ("_handle") {
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
            handlers.push(Handler::RustProgram(sentinel.factory_ref()));
        } else {
            handlers.push(Handler::Python(PyShared::new(item.unbind())));
        }
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

// ---------------------------------------------------------------------------
// SchedulerState implementation
// ---------------------------------------------------------------------------

impl SchedulerState {
    pub fn new() -> Self {
        SchedulerState {
            ready: VecDeque::new(),
            ready_waiters: VecDeque::new(),
            tasks: HashMap::new(),
            promises: HashMap::new(),
            waiters: HashMap::new(),
            next_task: 0,
            next_promise: 0,
            current_task: None,
        }
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

    pub fn save_task_store(&mut self, task_id: TaskId, store: &RustStore) {
        if let Some(state) = self.tasks.get_mut(&task_id) {
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
                }
                _ => {}
            }
        }
    }

    pub fn load_task_store(&self, task_id: TaskId, store: &mut RustStore) {
        if let Some(state) = self.tasks.get(&task_id) {
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
        }
    }

    pub fn mark_task_done(&mut self, task_id: TaskId, result: Result<Value, PyException>) {
        if let Some(state) = self.tasks.remove(&task_id) {
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
        }
    }

    pub fn wake_waiters(&mut self, waitable: Waitable) {
        if let Some(waiters) = self.waiters.remove(&waitable) {
            for waiter in waiters {
                let waiter_id = waiter.cont_id;
                for pending in self.waiters.values_mut() {
                    pending.retain(|k| k.cont_id != waiter_id);
                }
                let already_ready = self
                    .ready_waiters
                    .iter()
                    .any(|k| k.cont_id == waiter_id);
                if !already_ready {
                    self.ready_waiters.push_back(waiter);
                }
            }
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
        if results.len() == 1 {
            Some(results.into_iter().next().unwrap())
        } else {
            Some(Value::List(results))
        }
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

    pub fn wait_on_all(&mut self, items: &[Waitable], k: Continuation) {
        // Register k as waiting on all items that aren't done yet
        for item in items {
            let done = match item {
                Waitable::Task(tid) => matches!(self.tasks.get(tid), Some(TaskState::Done { .. })),
                Waitable::Promise(pid) | Waitable::ExternalPromise(pid) => {
                    matches!(self.promises.get(pid), Some(PromiseState::Done(_)))
                }
            };
            if !done {
                self.waiters.entry(*item).or_default().push(k.clone());
            }
        }
    }

    pub fn wait_on_any(&mut self, items: &[Waitable], k: Continuation) {
        // Register k as waiting on any of the items
        for item in items {
            let done = match item {
                Waitable::Task(tid) => matches!(self.tasks.get(tid), Some(TaskState::Done { .. })),
                Waitable::Promise(pid) | Waitable::ExternalPromise(pid) => {
                    matches!(self.promises.get(pid), Some(PromiseState::Done(_)))
                }
            };
            if !done {
                self.waiters.entry(*item).or_default().push(k.clone());
            }
        }
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
    pub fn transfer_next_or(&mut self, k: Continuation, store: &mut RustStore) -> RustProgramStep {
        if let Some(task_id) = self.ready.pop_front() {
            if let Some(task_k) = self.task_cont(task_id) {
                // Save current task's store before switching away
                if let Some(old_id) = self.current_task {
                    self.save_task_store(old_id, store);
                }
                // Load new task's store
                self.load_task_store(task_id, store);
                self.current_task = Some(task_id);
                return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Transfer {
                    continuation: task_k,
                    value: Value::Unit,
                }));
            }
        }
        if let Some(waiter) = self.ready_waiters.pop_front() {
            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Transfer {
                continuation: waiter,
                value: Value::Unit,
            }));
        }
        // No ready tasks, resume the caller
        RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Transfer {
            continuation: k,
            value: Value::Unit,
        }))
    }
}

// ---------------------------------------------------------------------------
// SchedulerPhase (internal to scheduler program)
// ---------------------------------------------------------------------------

#[derive(Debug)]
enum SchedulerPhase {
    Idle,
    SpawnPending {
        k_user: Continuation,
        store_mode: StoreMode,
        store_snapshot: Option<RustStore>,
    },
}

// ---------------------------------------------------------------------------
// SchedulerProgram + RustHandlerProgram impl
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
}

impl RustHandlerProgram for SchedulerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k_user: Continuation,
        store: &mut RustStore,
    ) -> RustProgramStep {
        let sched_effect = if let Some(obj) = dispatch_into_python(effect.clone()) {
            match parse_scheduler_python_effect(&obj) {
                Ok(Some(se)) => se,
                Ok(None) => {
                    return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate {
                        effect: dispatch_from_shared(obj),
                    }))
                }
                Err(msg) => {
                    return RustProgramStep::Throw(PyException::type_error(format!(
                        "failed to parse scheduler effect: {msg}"
                    )))
                }
            }
        } else {
            #[cfg(test)]
            {
                return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate { effect }));
            }
            #[cfg(not(test))]
            {
                unreachable!("runtime Effect is always Python")
            }
        };

        match sched_effect {
            SchedulerEffect::Spawn {
                program,
                handlers,
                store_mode,
            } => {
                let store_snapshot = match store_mode {
                    StoreMode::Shared => None,
                    StoreMode::Isolated { .. } => Some(store.clone()),
                };
                self.phase = SchedulerPhase::SpawnPending {
                    k_user,
                    store_mode,
                    store_snapshot,
                };
                // Yield CreateContinuation -- the VM will create an unstarted continuation
                // and resume us with the result
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::CreateContinuation {
                    expr: PyShared::new(program),
                    handlers,
                    handler_identities: vec![],
                }))
            }

            SchedulerEffect::TaskCompleted { task, result } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                state.save_task_store(task, store);
                state.mark_task_done(task, result);
                state.wake_waiters(Waitable::Task(task));
                state.transfer_next_or(k_user, store)
            }

            SchedulerEffect::Gather { items } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                if let Some(results) = state.try_collect(&items) {
                    state.merge_gather_logs(&items, store);
                    return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Transfer {
                        continuation: k_user,
                        value: results,
                    }));
                }
                state.wait_on_all(&items, k_user.clone());
                state.transfer_next_or(k_user, store)
            }

            SchedulerEffect::Race { items } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                if let Some(result) = state.try_race(&items) {
                    return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Transfer {
                        continuation: k_user,
                        value: result,
                    }));
                }
                state.wait_on_any(&items, k_user.clone());
                state.transfer_next_or(k_user, store)
            }

            SchedulerEffect::CreatePromise => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                let pid = state.alloc_promise_id();
                state.promises.insert(pid, PromiseState::Pending);
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Transfer {
                    continuation: k_user,
                    value: Value::Promise(PromiseHandle { id: pid }),
                }))
            }

            SchedulerEffect::CompletePromise { promise, value } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                state
                    .promises
                    .insert(promise, PromiseState::Done(Ok(value)));
                state.wake_waiters(Waitable::Promise(promise));
                state.transfer_next_or(k_user, store)
            }

            SchedulerEffect::FailPromise { promise, error } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                state
                    .promises
                    .insert(promise, PromiseState::Done(Err(error)));
                state.wake_waiters(Waitable::Promise(promise));
                state.transfer_next_or(k_user, store)
            }

            SchedulerEffect::CreateExternalPromise => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                let pid = state.alloc_promise_id();
                state.promises.insert(pid, PromiseState::Pending);
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Transfer {
                    continuation: k_user,
                    value: Value::ExternalPromise(ExternalPromise { id: pid }),
                }))
            }
        }
    }

    fn resume(&mut self, value: Value, _store: &mut RustStore) -> RustProgramStep {
        match std::mem::replace(&mut self.phase, SchedulerPhase::Idle) {
            SchedulerPhase::SpawnPending {
                k_user,
                store_mode,
                store_snapshot,
            } => {
                // Value should be the continuation created by CreateContinuation
                let cont = match value {
                    Value::Continuation(c) => c,
                    _ => {
                        return RustProgramStep::Throw(PyException::type_error(
                            "expected continuation from CreateContinuation, got unexpected type".to_string(),
                        ));
                    }
                };

                let task_store = match store_mode {
                    StoreMode::Shared => TaskStore::Shared,
                    StoreMode::Isolated { merge } => {
                        match store_snapshot {
                            Some(snapshot) => TaskStore::Isolated {
                                store: snapshot,
                                merge,
                            },
                            None => {
                                return RustProgramStep::Throw(PyException::runtime_error(
                                    "isolated spawn missing store snapshot".to_string(),
                                ))
                            }
                        }
                    }
                };

                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                let task_id = state.alloc_task_id();
                state.tasks.insert(
                    task_id,
                    TaskState::Pending {
                        cont,
                        store: task_store,
                    },
                );
                state.ready.push_back(task_id);

                // Transfer back to caller with the task handle
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Transfer {
                    continuation: k_user,
                    value: Value::Task(TaskHandle { id: task_id }),
                }))
            }

            SchedulerPhase::Idle => {
                // Unexpected resume
                RustProgramStep::Throw(PyException::runtime_error(
                    "Unexpected resume in scheduler: no pending operation".to_string(),
                ))
            }
        }
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
    }
}

// ---------------------------------------------------------------------------
// SchedulerHandler + RustProgramHandler impl
// ---------------------------------------------------------------------------

#[derive(Clone)]
pub struct SchedulerHandler {
    state: Arc<Mutex<SchedulerState>>,
}

impl std::fmt::Debug for SchedulerHandler {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SchedulerHandler").finish()
    }
}

impl SchedulerHandler {
    pub fn new() -> Self {
        SchedulerHandler {
            state: Arc::new(Mutex::new(SchedulerState::new())),
        }
    }
}

impl RustProgramHandler for SchedulerHandler {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        dispatch_ref_as_python(effect)
            .is_some_and(|obj| parse_scheduler_python_effect(obj).ok().flatten().is_some())
    }

    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(SchedulerProgram::new(
            self.state.clone(),
        ))))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pyo3::types::PyDictMethods;
    use pyo3::Python;

    fn make_test_continuation() -> Continuation {
        use crate::ids::{Marker, SegmentId};
        use crate::segment::Segment;

        let marker = Marker::fresh();
        let seg = Segment::new(marker, None, vec![marker]);
        let seg_id = SegmentId::from_index(0);
        Continuation::capture(&seg, seg_id, None)
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
        Python::attach(|py| {
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class EffectBase:\n    __doeff_effect_base__ = True\n\nclass CreatePromise(EffectBase):\n    __doeff_scheduler_create_promise__ = True\n    pass\nobj = CreatePromise()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap().unbind();
            assert!(handler.can_handle(&Effect::from_shared(PyShared::new(obj))));
        });
        assert!(!handler.can_handle(&Effect::Get {
            key: "x".to_string()
        }));
    }

    #[test]
    fn test_scheduler_handler_can_handle_python_spawn_effect() {
        Python::attach(|py| {
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class EffectBase:\n    __doeff_effect_base__ = True\n\nclass SpawnEffect(EffectBase):\n    __doeff_scheduler_spawn__ = True\n    def __init__(self):\n        self.program = None\n        self.handlers = []\n        self.store_mode = 'shared'\n\nobj = SpawnEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap().unbind();
            let effect = Effect::from_shared(PyShared::new(obj));
            let handler = SchedulerHandler::new();
            assert!(
                handler.can_handle(&effect),
                "SPEC GAP: scheduler should claim opaque Python scheduler effects"
            );
        });
    }

    #[test]
    fn test_scheduler_program_start_from_python_spawn_effect() {
        Python::attach(|py| {
            let mut store = RustStore::new();
            let k = make_test_continuation();

            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class EffectBase:\n    __doeff_effect_base__ = True\n\nclass SpawnEffect(EffectBase):\n    __doeff_scheduler_spawn__ = True\n    def __init__(self):\n        self.program = None\n        self.handlers = []\n        self.store_mode = 'shared'\n\nobj = SpawnEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap().unbind();
            let effect = Effect::from_shared(PyShared::new(obj));

            let handler = SchedulerHandler::new();
            let program = handler.create_program();
            let step = {
                let mut guard = program.lock().unwrap();
                guard.start(py, effect, k, &mut store)
            };
            assert!(
                matches!(
                    step,
                    RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::CreateContinuation {
                        ..
                    }))
                ),
                "SPEC GAP: scheduler opaque SpawnEffect should yield CreateContinuation"
            );
        });
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
        assert_eq!(result.unwrap().as_int(), Some(77));
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
        state.save_task_store(tid, &store);

        // Load back
        let mut loaded_store = RustStore::new();
        state.load_task_store(tid, &mut loaded_store);
        assert_eq!(loaded_store.get("key").unwrap().as_int(), Some(42));
    }
}
