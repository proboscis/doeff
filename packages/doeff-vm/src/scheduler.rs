//! Scheduler types for cooperative multitasking.
//!
//! The scheduler is a RustProgramHandler that manages tasks, promises,
//! and cooperative scheduling via Transfer-only semantics.

use std::collections::{HashMap, VecDeque};

use pyo3::prelude::*;

use crate::continuation::Continuation;
use crate::handler::Handler;
use crate::ids::{PromiseId, TaskId};
use crate::step::PyException;
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
}
