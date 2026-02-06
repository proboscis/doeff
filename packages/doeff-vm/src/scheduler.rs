//! Scheduler types for cooperative multitasking.
//!
//! The scheduler is a RustProgramHandler that manages tasks, promises,
//! and cooperative scheduling via Transfer-only semantics.

use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};

use pyo3::prelude::*;

use crate::continuation::Continuation;
use crate::effect::Effect;
use crate::handler::{Handler, RustHandlerProgram, RustProgramHandler, RustProgramRef, RustProgramStep};
use crate::ids::{PromiseId, TaskId};
use crate::step::{ControlPrimitive, PyException, Yielded};
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
    pub tasks: HashMap<TaskId, TaskState>,
    pub promises: HashMap<PromiseId, PromiseState>,
    pub waiters: HashMap<Waitable, Vec<Continuation>>,
    pub next_task: u64,
    pub next_promise: u64,
    pub current_task: Option<TaskId>,
}

// ---------------------------------------------------------------------------
// SchedulerState implementation
// ---------------------------------------------------------------------------

impl SchedulerState {
    pub fn new() -> Self {
        SchedulerState {
            ready: VecDeque::new(),
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
                TaskState::Pending { store: TaskStore::Isolated { store: ref mut task_store, .. }, .. } => {
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
            if let TaskStore::Isolated { store: task_store, .. } = task_store {
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
            self.tasks.insert(task_id, TaskState::Done { result, store: task_store });
        }
    }

    pub fn wake_waiters(&mut self, waitable: Waitable) {
        if let Some(_waiters) = self.waiters.remove(&waitable) {
            // For now, wake_waiters doesn't need to enqueue anything directly
            // because gather/race will re-check try_collect/try_race
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
                Waitable::Task(task_id) => {
                    match self.tasks.get(task_id) {
                        Some(TaskState::Done { result: Ok(v), .. }) => results.push(v.clone()),
                        Some(TaskState::Done { result: Err(_), .. }) => return None,
                        _ => return None,
                    }
                }
                Waitable::Promise(pid) => {
                    match self.promises.get(pid) {
                        Some(PromiseState::Done(Ok(v))) => results.push(v.clone()),
                        Some(PromiseState::Done(Err(_))) => return None,
                        _ => return None,
                    }
                }
            }
        }
        // All items are done. Return as a Python-compatible list value.
        // For now, return a simple representation. We'd need PyO3 for a real list.
        // Use Value::Python with a list, but we can't create Py<PyAny> without GIL.
        // So return a special encoding. For Rust-only tests, return the first result for single items,
        // or Value::None for the list case (will be handled properly in integration).
        if results.len() == 1 {
            Some(results.into_iter().next().unwrap())
        } else {
            // TODO: Create a proper list value when we have GIL access
            Some(Value::None) // placeholder for list of results
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
                Waitable::Promise(pid) => {
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
                Waitable::Promise(pid) => matches!(self.promises.get(pid), Some(PromiseState::Done(_))),
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
                Waitable::Promise(pid) => matches!(self.promises.get(pid), Some(PromiseState::Done(_))),
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
            if let TaskStore::Isolated { store: task_store, merge: StoreMergePolicy::LogsOnly } = task_store {
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
    pub fn transfer_next_or(&mut self, k: Continuation, _store: &mut RustStore) -> RustProgramStep {
        if let Some(task_id) = self.ready.pop_front() {
            if let Some(task_k) = self.task_cont(task_id) {
                self.current_task = Some(task_id);
                return RustProgramStep::Yield(Yielded::Primitive(ControlPrimitive::Transfer {
                    k: task_k,
                    value: Value::Unit,
                }));
            }
        }
        // No ready tasks, resume the caller
        RustProgramStep::Yield(Yielded::Primitive(ControlPrimitive::Transfer {
            k,
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
    fn start(&mut self, effect: Effect, k_user: Continuation, store: &mut RustStore) -> RustProgramStep {
        let Effect::Scheduler(sched_effect) = effect else {
            // Not our effect, delegate
            return RustProgramStep::Yield(Yielded::Primitive(ControlPrimitive::Delegate));
        };

        match sched_effect {
            SchedulerEffect::Spawn { program, handlers, store_mode } => {
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
                RustProgramStep::Yield(Yielded::Primitive(ControlPrimitive::CreateContinuation {
                    program,
                    handlers,
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
                    return RustProgramStep::Yield(Yielded::Primitive(ControlPrimitive::Transfer {
                        k: k_user,
                        value: results,
                    }));
                }
                state.wait_on_all(&items, k_user.clone());
                state.transfer_next_or(k_user, store)
            }

            SchedulerEffect::Race { items } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                if let Some(result) = state.try_race(&items) {
                    return RustProgramStep::Yield(Yielded::Primitive(ControlPrimitive::Transfer {
                        k: k_user,
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
                RustProgramStep::Yield(Yielded::Primitive(ControlPrimitive::Transfer {
                    k: k_user,
                    value: Value::Promise(PromiseHandle { id: pid }),
                }))
            }

            SchedulerEffect::CompletePromise { promise, value } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                state.promises.insert(promise, PromiseState::Done(Ok(value)));
                state.wake_waiters(Waitable::Promise(promise));
                state.transfer_next_or(k_user, store)
            }

            SchedulerEffect::FailPromise { promise, error } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                state.promises.insert(promise, PromiseState::Done(Err(error)));
                state.wake_waiters(Waitable::Promise(promise));
                state.transfer_next_or(k_user, store)
            }

            SchedulerEffect::CreateExternalPromise => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                let pid = state.alloc_promise_id();
                state.promises.insert(pid, PromiseState::Pending);
                RustProgramStep::Yield(Yielded::Primitive(ControlPrimitive::Transfer {
                    k: k_user,
                    value: Value::ExternalPromise(ExternalPromise { id: pid }),
                }))
            }
        }
    }

    fn resume(&mut self, value: Value, _store: &mut RustStore) -> RustProgramStep {
        match std::mem::replace(&mut self.phase, SchedulerPhase::Idle) {
            SchedulerPhase::SpawnPending { k_user, store_mode, store_snapshot } => {
                // Value should be the continuation created by CreateContinuation
                let cont = match value {
                    Value::Continuation(c) => c,
                    _ => {
                        return RustProgramStep::Return(Value::None); // error case
                    }
                };

                let task_store = match store_mode {
                    StoreMode::Shared => TaskStore::Shared,
                    StoreMode::Isolated { merge } => {
                        match store_snapshot {
                            Some(snapshot) => TaskStore::Isolated { store: snapshot, merge },
                            None => TaskStore::Shared, // fallback
                        }
                    }
                };

                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                let task_id = state.alloc_task_id();
                state.tasks.insert(task_id, TaskState::Pending { cont, store: task_store });
                state.ready.push_back(task_id);

                // Transfer back to caller with the task handle
                RustProgramStep::Yield(Yielded::Primitive(ControlPrimitive::Transfer {
                    k: k_user,
                    value: Value::Task(TaskHandle { id: task_id }),
                }))
            }

            SchedulerPhase::Idle => {
                // Unexpected resume
                RustProgramStep::Return(Value::None)
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
    fn can_handle(&self, effect: &Effect) -> bool {
        matches!(effect, Effect::Scheduler(_))
    }

    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(SchedulerProgram::new(self.state.clone()))))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
        assert!(matches!(state.promises.get(&pid), Some(PromiseState::Pending)));

        state.promises.insert(pid, PromiseState::Done(Ok(Value::Int(42))));
        assert!(matches!(state.promises.get(&pid), Some(PromiseState::Done(Ok(Value::Int(42))))));
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
        assert!(handler.can_handle(&Effect::Scheduler(SchedulerEffect::CreatePromise)));
        assert!(!handler.can_handle(&Effect::Get { key: "x".to_string() }));
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

        state.tasks.insert(tid, TaskState::Pending {
            cont,
            store: TaskStore::Isolated {
                store: store.clone(),
                merge: StoreMergePolicy::LogsOnly,
            },
        });

        // Save updated store
        store.put("key".to_string(), Value::Int(42));
        state.save_task_store(tid, &store);

        // Load back
        let mut loaded_store = RustStore::new();
        state.load_task_store(tid, &mut loaded_store);
        assert_eq!(loaded_store.get("key").unwrap().as_int(), Some(42));
    }
}
