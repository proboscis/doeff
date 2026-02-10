# SPEC-SCHED-001: Cooperative Scheduling for the Rust VM

## Status: Draft (Revision 5)

## Summary

Cooperative scheduling for the doeff Rust VM. The scheduler is a Rust
`RustHandlerProgram` handler that manages `Spawn`, `Wait`, `Gather`, `Race`,
`Cancel`, `Promise`, `ExternalPromise`, and `TaskCompleted` effects. All
effect types are `#[pyclass]` structs exposed to Python.

**Key properties**:

- **Per-yield preemption**: Every effect from a task is a preemption point.
  A `SchedulerYield` effect is inserted after each step via an envelope
  wrapper, giving the scheduler round-robin control.
- **No threads**: Single-threaded cooperative scheduling. External events
  use `ExternalPromise` (§CreateExternalPromise).
- **Waitable protocol**: Wait/Gather/Race accept any `Waitable` (Task or Future).
- **Error handling**: fail-fast Gather, error propagation on Wait/Race,
  TaskCancelledError, cooperative cancellation.
- **Store isolation**: state/log isolated per task, env shared.

```
User code              Envelope              Scheduler handler        VM
─────────              ────────              ─────────────────        ──
yield Get("x") ──────► yield Get("x") ─────► (state handler) ──────► resolve
               ◄────── result ◄──────────── result ◄────────────────
                       yield SchedYield ───► start(SchedYield, k)
                                             save task, pick next
                                             Transfer(k_next) ──────► switch
```

## Related Specs

| Spec | Relationship |
|------|-------------|
| SPEC-008 | Rust VM spec. §Built-in Scheduler Handler defers to this spec. |
| SPEC-008 §Continuation | Continuation primitives (Transfer, TransferThrow, ResumeContinuation, CreateContinuation). |
| SPEC-EFF-011 | Await effect (asyncio bridge). Orthogonal to scheduler. |
| SPEC-EFF-012 | Safe wrapper. Uses Ok/Err from SPEC-EFF-013. |
| SPEC-EFF-013 | Result types (Ok/Err). TaskCompleted.result is Ok or Err. |
| ~~SPEC-EFF-005~~ | **DEPRECATED.** Superseded by this spec. |
| ~~SPEC-EFF-007~~ | **DEPRECATED.** Superseded by §Waitable Types in this spec. |
| ~~SPEC-EFF-010~~ | **DEPRECATED.** Superseded by §CreateExternalPromise and §Promise vs ExternalPromise in this spec. |

### Out of scope (dedicated specs exist)

- **Await effect** (SPEC-EFF-011): Bridging Python asyncio coroutines into doeff programs
- **Safe wrapper** (SPEC-EFF-012): Collecting partial results from Gather (error-tolerant aggregation)
- **Result types** (SPEC-EFF-013): Rust Ok/Err types used by TaskCompleted and Safe

## Motivation

This spec provides the complete scheduler design for the Rust VM, covering:
per-yield preemption via envelope wrapping, continuation-state-aware dispatch
(`ResumeContinuation` for unstarted, `Transfer` for started), typed waiters
(Wait/Gather/Race), fail-fast error handling, cooperative cancellation,
promise/external-promise support, and store isolation semantics — all aligned
to the concurrency semantics defined in this spec.

## Core Principle: No Scheduler Loop, No Threads

The scheduler is a **handler**, not a loop. It processes one effect at a time
and yields one `DoCtrl` back to the VM. The "loop" is the VM's own step loop
dispatching effects to handlers repeatedly.

- Scheduler cannot "run a task to completion" in one handler invocation
- Each task yield re-enters the scheduler as a new effect dispatch
- Scheduler tracks state across invocations via `SchedulerState`
- **No threads**: all execution on main thread
- **External events**: `ExternalPromise` (§CreateExternalPromise) for I/O, timers

## Continuation States

| State | Created by | Valid operations |
|-------|-----------|-----------------|
| **Unstarted** (`started=false`) | `CreateContinuation` | `ResumeContinuation`, `Eval` |
| **Started** (`started=true`) | `Continuation::capture()` | `Resume`, `Transfer` |

**Invariant S1**: `Transfer(k)` requires `k.started == true`.
**Invariant S2**: `ResumeContinuation(k)` accepts both started and unstarted.

## Waitable Types

All types are Rust `#[pyclass]` structs exposed to Python via pyo3.

Wait, Gather, and Race accept any **Waitable** — either a `Task` (from
Spawn) or a `Future` (from `Promise.future`).

```
Waitable (trait / duck-type)   ← what Wait/Gather/Race accept
├── PyTask                     ← from Spawn, wraps TaskId
└── PyFuture                   ← from Promise.future, wraps PromiseId
```

### Rust types

```rust
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct TaskId(pub u64);

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct PromiseId(pub u64);

/// Internal discriminant. The scheduler resolves a user-facing PyTask/PyFuture
/// to this enum for registry lookup.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Waitable {
    Task(TaskId),
    Promise(PromiseId),
    ExternalPromise(PromiseId),
}

/// Task handle returned by Spawn. Exposed to Python as `Task`.
#[pyclass(frozen, name = "Task")]
pub struct PyTask {
    #[pyo3(get)]
    pub id: u64,
}

/// Read-side handle for a value that will arrive. Exposed to Python as `Future`.
#[pyclass(frozen, name = "Future")]
pub struct PyFuture {
    #[pyo3(get)]
    pub id: u64,
}

/// Write-side handle for completing a Future. Exposed to Python as `Promise`.
#[pyclass(frozen, name = "Promise")]
pub struct PyPromise {
    #[pyo3(get)]
    pub id: u64,
}

#[pymethods]
impl PyPromise {
    /// Get the read-side Future for this Promise.
    #[getter]
    fn future(&self) -> PyFuture {
        PyFuture { id: self.id }
    }
}

/// Write-side handle for external completion. Exposed to Python as `ExternalPromise`.
/// Unlike Promise, completion happens via method call (not effect), enabling
/// cross-process and cross-thread resolution.
#[pyclass(frozen, name = "ExternalPromise")]
pub struct PyExternalPromise {
    #[pyo3(get)]
    pub id: u64,

    /// UUID v4 for cross-process serialization (ray, multiprocess, IPC).
    /// External code receives this string and uses it to route completions
    /// back to the scheduler's queue.
    #[pyo3(get)]
    pub uuid: String,

    /// Reference to scheduler's thread-safe completion queue.
    completion_queue: Arc<Mutex<VecDeque<ExternalCompletion>>>,
}

#[pymethods]
impl PyExternalPromise {
    /// Get the read-side Future for this ExternalPromise.
    #[getter]
    fn future(&self) -> PyFuture {
        PyFuture { id: self.id }
    }

    /// Complete the promise with a value. Thread-safe, non-blocking.
    /// Called from external code (asyncio, ray, threads).
    fn complete(&self, value: PyObject) {
        let mut queue = self.completion_queue.lock().unwrap();
        queue.push_back(ExternalCompletion {
            promise_id: PromiseId(self.id),
            result: Ok(value.into()),
        });
    }

    /// Fail the promise with an error. Thread-safe, non-blocking.
    fn fail(&self, error: PyObject) {
        let mut queue = self.completion_queue.lock().unwrap();
        queue.push_back(ExternalCompletion {
            promise_id: PromiseId(self.id),
            result: Err(error.into()),
        });
    }
}

#[pymethods]
impl PyTask {
    /// Request cooperative cancellation. Returns a Cancel effect.
    fn cancel(&self, py: Python) -> PyResult<PyObject> {
        let init = PyClassInitializer::from(PyEffectBase { tag: DoExprTag::Effect as u8 })
            .add_subclass(PyCancelEffect { task: self.clone().into_pyobject(py)?.into() });
        Py::new(py, init).map(|p| p.into_pyobject(py).into())
    }
}
```

### Waitable resolution

The scheduler extracts the `id` field from any `PyTask` or `PyFuture` and
resolves it to an internal `Waitable` enum variant. This is a **method on
SchedulerState** because resolving a `PyFuture` requires checking whether the
underlying promise is internal or external:

```rust
impl SchedulerState {
    fn resolve_waitable(&self, obj: &Bound<'_, PyAny>) -> PyResult<Waitable> {
        if let Ok(task) = obj.extract::<PyRef<PyTask>>() {
            Ok(Waitable::Task(TaskId(task.id)))
        } else if let Ok(future) = obj.extract::<PyRef<PyFuture>>() {
            // Check internal promises first, then external
            let pid = PromiseId(future.id);
            if self.promises.contains_key(&pid) {
                Ok(Waitable::Promise(pid))
            } else if self.external_promises.contains_key(&pid) {
                Ok(Waitable::ExternalPromise(pid))
            } else {
                Err(PyValueError::new_err(format!("unknown promise id: {}", future.id)))
            }
        } else {
            Err(PyTypeError::new_err("expected Task or Future (Waitable)"))
        }
    }
}
```

## Task Lifecycle

```
                  CreateContinuation
                        │
                        ▼
                  ┌───────────┐  cancel
  Spawn ────────► │  PENDING   │ ──────────────────────────► CANCELLED
                  └─────┬─────┘
                        │ ResumeContinuation (first run)
                        ▼
                  ┌───────────┐
              ┌──►│  RUNNING   │◄──────────────────────┐
              │   └──┬──┬──┬──┘                        │
              │      │  │  │                           │
              │      │  │  └── cancel (at next         │
              │      │  │      SchedulerYield)         │
              │      │  │            │                  │
              │      │  │            ▼                  │
              │      │  │       CANCELLED               │
              │      │  │                              │
              │      │  └── error ────► FAILED         │
              │      │                                 │
              │      ├── preempt (SchedulerYield)      │
              │      │         │                       │
              │      │         ▼                       │
              │      │   ┌───────────┐  cancel         │
              │      │   │ SUSPENDED  │ ──► CANCELLED  │
              │      │   └─────┬─────┘                 │
              │      │         │ schedule              │
              │      │         └───────────────────────┘
              │      │
              │      └── Wait/Gather/Race (pending waitable)
              │               │
              │               ▼
              │         ┌───────────┐  cancel
              │         │  BLOCKED   │ ──────► CANCELLED
              │         └─────┬─────┘
              │               │ waitable completes
              │               └───────────────────────┐
              │                                       │
              └── success ──► COMPLETED               │
                                                      │
                         (woken waiter → Transfer ────┘
                          back into RUNNING)
```

State names are canonical for the doeff scheduler.

**Transitions from each state**:

| From | To | Trigger |
|------|----|---------|
| PENDING | RUNNING | ResumeContinuation (scheduled) |
| PENDING | CANCELLED | Cancel effect received |
| RUNNING | SUSPENDED | SchedulerYield preemption |
| RUNNING | BLOCKED | Wait/Gather/Race on pending waitable |
| RUNNING | COMPLETED | Task returns successfully (TaskCompleted Ok) |
| RUNNING | FAILED | Task raises exception (TaskCompleted Err) |
| RUNNING | CANCELLED | cancel_requested flag checked at SchedulerYield |
| SUSPENDED | RUNNING | Transfer (scheduled from ready queue) |
| SUSPENDED | CANCELLED | Cancel effect received |
| BLOCKED | RUNNING | Waitable completes → waiter woken → Transfer |
| BLOCKED | CANCELLED | Cancel effect received |

## TaskStore

A `TaskStore` is the **per-task isolated snapshot** of mutable state. Only
`state` (Get/Put) and `log` (Tell) are isolated. `env` (Ask) is **shared**
across all tasks via `RustStore.env` — it is never snapshotted or swapped.

```rust
/// Per-task isolated state. Snapshotted from RustStore at Spawn time.
/// Only state and log are isolated; env stays shared in RustStore.
#[derive(Debug, Clone)]
pub struct TaskStore {
    pub state: HashMap<String, Value>,
    pub log: Vec<Value>,
}
```

**Context switch protocol**:
1. **Save** current task: copy `RustStore.state` and `RustStore.log` into
   `TaskState::{Suspended,Blocked}.store`
2. **Load** next task: copy next task's `TaskStore.state` and `TaskStore.log`
   into `RustStore.state` and `RustStore.log`
3. `RustStore.env` is **not touched** — it is shared across all tasks

This means the Reader handler (`Ask`) works normally — it reads from
`RustStore.env` which is the same for all tasks. No `env_cache` needed.

## TaskState

```rust
#[derive(Debug)]
pub enum TaskState {
    /// Task created, never started. Continuation is unstarted.
    Pending {
        cont: Continuation,
        store: TaskStore,
    },
    /// Task is currently executing in the VM.
    Running {
        store: TaskStore,
    },
    /// Task preempted by scheduler. Ready to resume.
    /// Continuation is started (captured during dispatch).
    Suspended {
        cont: Continuation,
        store: TaskStore,
    },
    /// Task is blocked waiting for waitable(s) (Wait/Gather/Race).
    /// Continuation is held by the Waiter, not stored here.
    Blocked {
        store: TaskStore,
    },
    /// Task completed successfully. Store preserved for Gather log merging.
    Completed {
        result: Value,
        store: TaskStore,
    },
    /// Task failed with an error. Store preserved for Gather log merging.
    Failed {
        error: PyException,
        store: TaskStore,
    },
    /// Task was cancelled cooperatively. Store dropped (no merge needed).
    Cancelled,
}
```

**Changes from Revision 1**:
- `Ready { first_run }` split into `Pending` (unstarted) and `Suspended` (started)
- `first_run` flag eliminated — state variant determines the correct DoCtrl
- `Blocked` added for tasks waiting on waitables (Wait/Gather/Race)
- `Cancelled` added
- `cancel_requested` set on SchedulerState for cooperative cancellation

**Changes from Revision 2**:
- `throw(k, error)` → `TransferThrow(k, error)` (SPEC-008 DoCtrl alignment)
- Envelope catches both success and failure — no `FlatMap` wrapping needed
- `SchedulerYield.task_id` type: `u64` (removed duplicate definition)
- `resolve_waitable` is now a method on `SchedulerState` with `&Bound<'_, PyAny>`
- `PyTask::cancel()` fixed: takes `py: Python`, returns `PyResult<PyObject>`
- `TaskStore` defined: `{ state, log }` — only state/log isolated, env shared
- Removed `env_cache` from `SchedulerState` — `RustStore.env` is shared
- Spawn handler uses `GetHandlers` DoCtrl before `CreateContinuation`
- Spawn returns `PyTask` (not undefined `TaskHandle`)
- Added `CreatePromise`/`CompletePromise`/`FailPromise`/`CreateExternalPromise` handler pseudocode
- Added `PromiseState` enum
- Separated `promises` and `external_promises` maps with shared `next_promise_id`
- Specified `can_handle`, `remove_waiter_refs`, `drain_external_promises`, `load_task_store`/`save_task_store`
- `resume_task` uses `std::mem::replace` for ownership
- Completed lifecycle diagram with BLOCKED state and all transitions
- Removed SPEC-008 Contradictions table (SPEC-008 now defers to this spec)

**Changes from Revision 3**:
- TaskCompleted uses single `result: PyObject` field (always Ok/Err per SPEC-EFF-013)
- Removed `value: Option<PyObject>` / `error: Option<PyObject>` two-field workaround
- Added SPEC-EFF-011, SPEC-EFF-012, SPEC-EFF-013 to Related Specs table
- Updated "Out of scope" to "Out of scope (dedicated specs exist)"
- Defined `is_completed`, `first_error_in`, `collect_results_in_input_order` helpers
- Cancel handler now explicitly specifies `Err(TaskCancelledError)` for all wake paths
- PySpawnEffect gains `handlers` (optional override) and `store_mode` fields
- Spawn handler distinguishes explicit handler override vs GetHandlers inheritance
- Completed/Failed variants now carry `store: TaskStore` (needed for Gather log merge)
- TaskCompleted handler saves task store before marking terminal
- `load_task_store`/`save_current_task_store` are free functions (not SchedulerState methods)
- `switch_to_next`/`resume_task` take `&mut RustStore` parameter
- `resume_task` loads store from extracted TaskStore (not by task_id lookup after mem::replace)

**Changes from Revision 4**:
- `ReadyWaiter` gains `task_id: TaskId` field — required for loading blocked task's store on wake
- `switch_to_next` loads woken task's store (Blocked → Running transition) before Transfer
- Wait/Gather/Race blocking paths now save store before marking Blocked
- All `switch_to_next()` calls pass `store` parameter (Wait, Gather, Race, TaskCompleted)
- Removed `store_mode` from `PySpawnEffect` — always snapshot parent's store (clone-on-switch
  design cannot support shared stores; future spec if needed)
- Cancel handler returns `Unit` (was `None`) — consistent with CompletePromise/FailPromise
- Single-task SchedulerYield optimization clarified: cancel check always runs

## Per-Yield Preemption

### Semantic Model

Every effect dispatch from a scheduled task is a preemption point. The
scheduler interleaves tasks in round-robin order at each yield.

### Mechanism: Scheduler Envelope

Each spawned program is wrapped in a **scheduler envelope** that inserts
a `SchedulerYield` effect after each step of the original program AND
catches task completion (both success and failure):

```
Original task execution:         Enveloped execution:
  yield Get("x")                   yield Get("x")        ← handled by state handler
                                   yield SchedulerYield   ← scheduler preemption point
  yield Put("x", x+1)             yield Put("x", x+1)   ← handled by state handler
                                   yield SchedulerYield   ← scheduler preemption point
  return x                        yield TaskCompleted(task_id, result=Ok(x))   ← notifies scheduler
  raise SomeError                  yield TaskCompleted(task_id, result=Err(e)) ← notifies scheduler
```

The envelope is a Python-level generator wrapper applied at Spawn time.
`SchedulerYield`, `TaskCompleted`, and `Perform` are Rust `#[pyclass]` types:

```python
def _scheduler_envelope(gen, task_id):
    """Wrap generator to insert SchedulerYield between each step.
    Catches both success (StopIteration) and failure (Exception),
    yielding TaskCompleted with Ok/Err result (SPEC-EFF-013).
    """
    result = None
    try:
        while True:
            do_expr = gen.send(result)
            result = yield do_expr
            _ = yield Perform(SchedulerYield(task_id))
    except StopIteration as e:
        yield Perform(TaskCompleted(task_id, result=Ok(e.value)))
    except Exception as e:
        yield Perform(TaskCompleted(task_id, result=Err(e)))
```

Because the envelope catches both success and failure, no `FlatMap` wrapper
is needed. The task wrapping is simply `Call(envelope)`.

### SchedulerYield Handling

```
Input:  SchedulerYield { task_id }
        k_envelope = envelope's continuation (started)

1. Save current task store: task_store = save_current_task_store(store)
2. Check self.cancel_requested.remove(&task_id)
   → If cancelled: mark task CANCELLED, drop k_envelope,
     wake_waiters_for(Task(task_id), &Err(TaskCancelledError)),
     switch_to_next(store)
3. Store TaskState::Suspended { cont: k_envelope, store: task_store }
4. Push task_id to back of ready queue
5. switch_to_next(store)
   → Picks next task (round-robin), switches via Transfer or ResumeContinuation
```

When the task is later scheduled:
- `Suspended { cont }` → `Transfer(cont, Value::Unit)` — resumes envelope
- Envelope receives Unit (discarded), continues to next step

### Optimization

When only one task is in the ready queue, SchedulerYield can skip queue
manipulation: immediately `Transfer(k_envelope, Value::Unit)`. The cancel
check (step 2) must still run — a single-task program can still be cancelled
by an external promise callback or a parent task's Cancel effect.

## Effect Types (all `#[pyclass]`)

All scheduler effects are `#[pyclass(extends=PyEffectBase, frozen)]` Rust
structs exposed to Python via pyo3.

```rust
#[pyclass(extends=PyEffectBase, frozen)]
pub struct PySpawnEffect {
    #[pyo3(get)] pub program: PyObject,      // ProgramLike
    #[pyo3(get)] pub handlers: PyObject,     // Option<list[Handler]> — None = inherit parent
}

#[pyclass(extends=PyEffectBase, frozen)]
pub struct PyWaitEffect {
    #[pyo3(get)] pub waitable: PyObject,   // Waitable (Task or Future)
}

#[pyclass(extends=PyEffectBase, frozen)]
pub struct PyGatherEffect {
    #[pyo3(get)] pub waitables: Vec<PyObject>,  // list[Waitable]
}

#[pyclass(extends=PyEffectBase, frozen)]
pub struct PyRaceEffect {
    #[pyo3(get)] pub waitables: Vec<PyObject>,  // list[Waitable]
}

#[pyclass(extends=PyEffectBase, frozen)]
pub struct PyCancelEffect {
    #[pyo3(get)] pub task: PyObject,     // PyTask
}

#[pyclass(extends=PyEffectBase, frozen)]
pub struct PySchedulerYield {
    #[pyo3(get)] pub task_id: u64,
}

// NOTE: After SPEC-EFF-013 unification, result is a single Ok(...) or Err(...)
// object (PyResultOk / PyResultErr from Rust). See SPEC-EFF-013 §TaskCompleted.
#[pyclass(extends=PyEffectBase, frozen)]
pub struct PySchedulerTaskCompleted {
    #[pyo3(get)] pub task_id: u64,
    #[pyo3(get)] pub result: PyObject,             // Ok(value) or Err(error)
}

#[pyclass(extends=PyEffectBase, frozen)]
pub struct PyCreatePromiseEffect;

#[pyclass(extends=PyEffectBase, frozen)]
pub struct PyCompletePromiseEffect {
    #[pyo3(get)] pub promise: PyObject,  // PyPromise
    #[pyo3(get)] pub value: PyObject,
}

#[pyclass(extends=PyEffectBase, frozen)]
pub struct PyFailPromiseEffect {
    #[pyo3(get)] pub promise: PyObject,  // PyPromise
    #[pyo3(get)] pub error: PyObject,
}

#[pyclass(extends=PyEffectBase, frozen)]
pub struct PyCreateExternalPromiseEffect;
```

### RaceResult Type

```rust
/// Return type of Race effect. Exposed to Python as `RaceResult`.
#[pyclass(frozen, name = "RaceResult")]
pub struct PyRaceResult {
    #[pyo3(get)] pub first: PyObject,    // PyTask or PyFuture — the winner
    #[pyo3(get)] pub value: PyObject,    // T — winner's value
    #[pyo3(get)] pub rest: Vec<PyObject>, // remaining PyTask/PyFuture (losers)
}
```

## Effect Handling

### Spawn

```
Input:  Spawn { program, handlers: opt_handlers }
        k_user = caller's continuation (started)

1. Allocate task_id
2. Wrap program in scheduler envelope:
   envelope = _scheduler_envelope(program, task_id)
   wrapped = Call(envelope)
   (No FlatMap needed — envelope handles TaskCompleted internally)
3. Snapshot parent's store for child isolation:
   child_store = save_current_task_store(store)
4. Determine handler chain:
   Case A: opt_handlers is not None (explicit override)
     → handlers = opt_handlers
     → Yield CreateContinuation { expr: wrapped, handlers }
     → VM creates unstarted k_task, resumes scheduler at step 6

   Case B: opt_handlers is None (inherit parent)
     → Yield GetHandlers
     → VM resumes scheduler with current handler chain at step 5

5. resume(handlers):         [only reached from Case B]
   Yield CreateContinuation { expr: wrapped, handlers }
   → VM creates unstarted k_task, resumes scheduler

6. resume(k_task):
   Store TaskState::Pending { cont: k_task, store: child_store }
   Push task_id to ready queue
   task = PyTask { id: task_id.0 }
   Yield Transfer(k_user, task)
```

`Transfer(k_user)` is valid: k_user was captured by start_dispatch (started=true).

**Handler inheritance**: By default (`handlers=None`), the child inherits
the parent's handler chain via `GetHandlers` DoCtrl. This includes the state,
reader, writer, and scheduler handlers — ensuring the child runs in the same
effect environment. When `handlers` is explicitly provided, those handlers
are used directly (skipping `GetHandlers`), allowing advanced use cases like
running a child with a different handler stack.

### Wait

```
Input:  Wait { waitable }       ← Task or Future (Waitable)
        k_user = caller's continuation

  self.resolve_waitable(waitable) → internal Waitable enum

Case 1: waitable is COMPLETED
  → Transfer(k_user, result)

Case 2: waitable is FAILED
  → TransferThrow(k_user, error)

Case 3: waitable is CANCELLED
  → TransferThrow(k_user, TaskCancelledError)

Case 4: waitable is PENDING
  → task_store = save_current_task_store(store)
  → Mark caller task as Blocked { store: task_store }
  → Register WaitWaiter { k_user, waitable }
  → switch_to_next(store)
```

### Gather

```
Input:  Gather { waitables }    ← list[Waitable]
        k_user = caller's continuation

  self.resolve_waitable(each) → Vec<Waitable>

Case 1: waitables is empty
  → Transfer(k_user, [])

Case 2: Any waitable FAILED (fail-fast)
  → TransferThrow(k_user, first_error)

Case 3: Any waitable CANCELLED (fail-fast)
  → TransferThrow(k_user, TaskCancelledError)

Case 4: All waitables COMPLETED
  → Collect results in input order
  → Transfer(k_user, results_list)

Case 5: Some waitables still PENDING
  → task_store = save_current_task_store(store)
  → Mark caller task as Blocked { store: task_store }
  → Register GatherWaiter { k_user, waitables }
  → switch_to_next(store)
```

**Fail-fast**: On first error/cancellation among gathered waitables, Gather
fails immediately. Remaining waitables continue running (NOT auto-cancelled).
Use `Safe` wrapper (SPEC-EFF-012) to collect partial results.

**Result ordering**: Results are in the same order as waitables were passed,
regardless of completion order.

### Race (returns RaceResult)

```
Input:  Race { waitables }      ← list[Waitable]
        k_user = caller's continuation

  self.resolve_waitable(each) → Vec<Waitable>

Case 1: Any waitable already COMPLETED
  → Pick first completed waitable
  → Build RaceResult { first, value, rest: remaining waitables }
  → Transfer(k_user, race_result)

Case 2: Any waitable already FAILED
  → TransferThrow(k_user, error)

Case 3: Any waitable already CANCELLED
  → TransferThrow(k_user, TaskCancelledError)

Case 4: All waitables PENDING
  → task_store = save_current_task_store(store)
  → Mark caller task as Blocked { store: task_store }
  → Register RaceWaiter { k_user, waitables }
  → switch_to_next(store)
```

**Loser semantics**: Losers continue running unless explicitly cancelled.
This is intentional — user may want loser results later.

### Cancel

```
Input:  Cancel { task }
        k_user = caller's continuation

1. Resolve task.id → target_task_id
2. Match target task state:
   Pending   → Remove from ready queue, mark CANCELLED,
               wake_waiters_for(Task(target), &Err(TaskCancelledError))
   Running   → self.cancel_requested.insert(target_task_id)
               (cooperative — checked at next SchedulerYield)
   Suspended → Mark CANCELLED, remove from ready queue,
               wake_waiters_for(Task(target), &Err(TaskCancelledError))
   Blocked   → Mark CANCELLED, remove target's own waiter registrations,
               wake_waiters_for(Task(target), &Err(TaskCancelledError))
   Completed/Failed/Cancelled → no-op
3. Transfer(k_user, Unit)  — cancel is non-blocking, returns immediately
```

For **Running** tasks, cancellation is **cooperative**. The flag is checked
at the next `SchedulerYield` preemption point:

```
In SchedulerYield handler:
  if self.cancel_requested.remove(&task_id):
    1. Mark task as CANCELLED
    2. Discard task's continuation (drop)
    3. Wake any waiters watching this task with TaskCancelledError
    4. switch_to_next()
```

Cancellation of tasks in different states:

| Target state | Cancel behavior |
|-------------|-----------------|
| Pending | Mark CANCELLED, wake waiters with `Err(TaskCancelledError)` |
| Running | Set `cancel_requested` flag, cancel at next SchedulerYield |
| Suspended | Mark CANCELLED, wake waiters with `Err(TaskCancelledError)` |
| Blocked | Mark CANCELLED, remove target's waiter, wake waiters with `Err(TaskCancelledError)` |
| Completed/Failed/Cancelled | No-op |

### TaskCompleted

```
Input:  TaskCompleted { task_id, result }      // result is Ok(value) or Err(error)

1. Save task's final store:
   final_store = save_current_task_store(store)
   (task was Running — its state/log are in RustStore)
2. Extract result (SPEC-EFF-013):
   result is Ok(value) → Transition task to Completed { result: value, store: final_store }
   result is Err(error) → Transition task to Failed { error, store: final_store }
3. wake_waiters_for(Waitable::Task(task_id), &completion_result)
4. switch_to_next(store)
```

Store must be saved BEFORE marking the task terminal. Gather's
`merge_gather_logs` reads the store from the Completed/Failed variant
to merge child logs into the parent.

### CreatePromise

```
Input:  CreatePromise { }
        k_user = caller's continuation

1. Allocate promise_id from next_promise_id counter
2. Store PromiseState::Pending in self.promises[promise_id]
3. promise = PyPromise { id: promise_id.0 }
4. Transfer(k_user, promise)
```

### CompletePromise

```
Input:  CompletePromise { promise, value }
        k_user = caller's continuation

1. Extract promise_id from promise.id
2. Assert self.promises[promise_id] is Pending
3. Transition to PromiseState::Completed { result: value }
4. wake_waiters_for(Waitable::Promise(promise_id))
5. Transfer(k_user, Unit)
```

### FailPromise

```
Input:  FailPromise { promise, error }
        k_user = caller's continuation

1. Extract promise_id from promise.id
2. Assert self.promises[promise_id] is Pending
3. Transition to PromiseState::Failed { error }
4. wake_waiters_for(Waitable::Promise(promise_id))
5. Transfer(k_user, Unit)
```

### CreateExternalPromise

```
Input:  CreateExternalPromise { }
        k_user = caller's continuation

1. Allocate promise_id from next_promise_id counter (shared with CreatePromise)
2. Generate uuid (UUID v4) for cross-process serialization
3. Store PromiseState::Pending in self.external_promises[promise_id]
4. Store self.uuid_to_promise_id[uuid] = promise_id
5. Create PyExternalPromise with:
   - id: promise_id.0
   - uuid: uuid string
   - complete(value): pushes ExternalCompletion to external_completions queue
   - fail(error): pushes ExternalCompletion to external_completions queue
6. Transfer(k_user, external_promise)
```

`PyExternalPromise.complete()` and `.fail()` are method calls (not effects)
that push to the thread-safe `external_completions` queue. The scheduler
drains this queue in `switch_to_next` (Priority 3).

**Cross-process usage**: External code (ray workers, subprocesses) receives
the `uuid` string. A completion adapter resolves the UUID back to the
scheduler's queue. The `id` (u64) is for in-process lookup only.

### PromiseState

```rust
#[derive(Debug)]
pub enum PromiseState {
    Pending,
    Completed { result: Value },
    Failed { error: PyException },
}
```

## Error Handling

### Wait: error propagation

If the awaited future is FAILED, Wait raises that error in the waiting task.
If the future is CANCELLED, Wait raises `TaskCancelledError`.

### Gather: fail-fast

On the first error from any gathered future, Gather fails immediately with
that error.

To collect all results including errors, wrap individual programs in `Safe`
before Spawn.

### Race: first error propagates

If the first future to complete has an error, Race raises that error.

### TaskCancelledError

A Rust-defined Python exception raised when waiting on a cancelled task:

```rust
pyo3::create_exception!(doeff_vm, TaskCancelledError, pyo3::exceptions::PyException);
```

## Store Semantics

### Isolated State/Log (Get/Put/Tell)

- Child task gets a **snapshot** of the parent's `state` and `log` at spawn
  time (see §TaskStore)
- Child's Get/Put/Tell don't affect parent
- Parent's Get/Put/Tell don't affect child
- `TaskStore` is part of `TaskState` and swapped on context switch

```python
@do
def example():
    yield Put("counter", 0)
    task = yield Spawn(increment())  # child sees counter=0
    yield Put("counter", 100)        # parent's change
    result = yield Wait(task)        # result == 1 (child's view)
    final = yield Get("counter")     # final == 100 (parent's view)
```

### Shared Env (Ask)

`RustStore.env` is **never isolated**. All tasks share the same `env` map
in the single `RustStore` instance. On context switch, only `state` and
`log` are swapped — `env` is left untouched.

This means:
- The Reader handler (`Ask`) works normally — reads from `RustStore.env`
- No `env_cache` is needed in `SchedulerState`
- All tasks see the same resolved Ask values (shared memoization)
- `env` is populated before scheduling starts (by the Reader handler)

## switch_to_next

Core scheduling decision. Picks the next thing to run:

```rust
fn switch_to_next(&mut self, store: &mut RustStore) -> RustProgramStep {
    // Priority 1: Ready waiter (Wait/Gather/Race completed)
    if let Some(ready) = self.ready_waiters.pop_front() {
        // Load the woken task's store and transition Blocked → Running
        let old = std::mem::replace(
            self.tasks.get_mut(&ready.task_id).expect("task exists"),
            TaskState::Cancelled,
        );
        match old {
            TaskState::Blocked { store: task_store } => {
                load_task_store(&task_store, store);
                self.tasks.insert(ready.task_id, TaskState::Running { store: task_store });
                self.current_task = Some(ready.task_id);
            }
            other => {
                self.tasks.insert(ready.task_id, other);
                panic!("ready waiter's task is not Blocked: {:?}", ready.task_id);
            }
        }
        return match ready.result {
            WaiterResult::Ok(value) => Transfer(ready.continuation, value),
            WaiterResult::Err(error) => TransferThrow(ready.continuation, error),
        };
    }

    // Priority 2: Ready task from queue
    if let Some(task_id) = self.ready_queue.pop_front() {
        return self.resume_task(task_id, store);
    }

    // Priority 3: Drain external promise completions
    self.drain_external_promises();
    if let Some(ready) = self.ready_waiters.pop_front() {
        let old = std::mem::replace(
            self.tasks.get_mut(&ready.task_id).expect("task exists"),
            TaskState::Cancelled,
        );
        match old {
            TaskState::Blocked { store: task_store } => {
                load_task_store(&task_store, store);
                self.tasks.insert(ready.task_id, TaskState::Running { store: task_store });
                self.current_task = Some(ready.task_id);
            }
            other => {
                self.tasks.insert(ready.task_id, other);
                panic!("ready waiter's task is not Blocked: {:?}", ready.task_id);
            }
        }
        return match ready.result {
            WaiterResult::Ok(value) => Transfer(ready.continuation, value),
            WaiterResult::Err(error) => TransferThrow(ready.continuation, error),
        };
    }

    // Priority 4: No work
    panic!("Scheduler deadlock: all tasks blocked, no external promises pending");
}

fn resume_task(&mut self, task_id: TaskId, store: &mut RustStore) -> RustProgramStep {
    // Take ownership of the TaskState to extract cont and task_store.
    let old = std::mem::replace(
        self.tasks.get_mut(&task_id).expect("task exists"),
        TaskState::Cancelled,  // placeholder, immediately overwritten
    );
    match old {
        TaskState::Pending { cont, store: task_store } => {
            // Unstarted → ResumeContinuation (Invariant S2)
            load_task_store(&task_store, store);
            self.tasks.insert(task_id, TaskState::Running { store: task_store });
            self.current_task = Some(task_id);
            ResumeContinuation(cont, Value::Unit)
        }
        TaskState::Suspended { cont, store: task_store } => {
            // Started → Transfer (Invariant S1 satisfied)
            load_task_store(&task_store, store);
            self.tasks.insert(task_id, TaskState::Running { store: task_store });
            self.current_task = Some(task_id);
            Transfer(cont, Value::Unit)
        }
        other => {
            // Put it back and panic
            self.tasks.insert(task_id, other);
            panic!("ready queue contains non-runnable task: {:?}", task_id);
        }
    }
}
```

**Invariant S3**: `resume_task` uses `ResumeContinuation` for `Pending`
(unstarted) and `Transfer` for `Suspended` (started).

## Waiter Design

### Types

```rust
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct WaiterId(pub u64);

/// Result of a waitable completion. Used by wake_waiters_for.
type CompletionResult = Result<Value, PyException>;

enum WaiterKind {
    /// Wait: single waitable.
    Wait { item: Waitable },

    /// Gather: all waitables must complete. Fail-fast on first error.
    Gather { items: Vec<Waitable> },

    /// Race: first waitable to complete wins.
    Race { items: Vec<Waitable> },
}

struct Waiter {
    id: WaiterId,
    task_id: TaskId,
    continuation: Continuation,
    kind: WaiterKind,
}

struct ReadyWaiter {
    task_id: TaskId,
    continuation: Continuation,
    result: WaiterResult,
}

enum WaiterResult {
    Ok(Value),
    Err(PyException),
}
```

### Wake Logic

```rust
fn wake_waiters_for(&mut self, completed_id: Waitable, result: &CompletionResult) {
    let waiter_ids = self.waiters_by_waitable.remove(&completed_id);

    for waiter_id in waiter_ids {
        let waiter = &self.all_waiters[waiter_id];
        match &waiter.kind {
            WaiterKind::Wait { .. } => {
                // Single waitable — wake immediately
                let waiter = self.all_waiters.remove(&waiter_id);
                self.ready_waiters.push_back(ReadyWaiter {
                    task_id: waiter.task_id,
                    continuation: waiter.continuation,
                    result: match result {
                        Ok(v) => WaiterResult::Ok(v.clone()),
                        Err(e) => WaiterResult::Err(e.clone()),
                    },
                });
            }

            WaiterKind::Gather { items } => {
                // Fail-fast: if this result is an error, wake immediately
                if let Err(e) = result {
                    let waiter = self.all_waiters.remove(&waiter_id);
                    self.remove_waiter_refs(waiter_id);
                    self.ready_waiters.push_back(ReadyWaiter {
                        task_id: waiter.task_id,
                        continuation: waiter.continuation,
                        result: WaiterResult::Err(e.clone()),
                    });
                    continue;
                }
                // Check if ALL items are now done
                if items.iter().all(|w| self.is_completed(w)) {
                    // Check for any errors among completed items
                    if let Some(err) = self.first_error_in(items) {
                        let waiter = self.all_waiters.remove(&waiter_id);
                        self.remove_waiter_refs(waiter_id);
                        self.ready_waiters.push_back(ReadyWaiter {
                            task_id: waiter.task_id,
                            continuation: waiter.continuation,
                            result: WaiterResult::Err(err),
                        });
                    } else {
                        let results = self.collect_results_in_input_order(items);
                        let waiter = self.all_waiters.remove(&waiter_id);
                        self.remove_waiter_refs(waiter_id);
                        self.ready_waiters.push_back(ReadyWaiter {
                            task_id: waiter.task_id,
                            continuation: waiter.continuation,
                            result: WaiterResult::Ok(Value::List(results)),
                        });
                    }
                }
                // Not all done: waiter stays registered on remaining items
            }

            WaiterKind::Race { items } => {
                // ANY completion wins
                match result {
                    Ok(value) => {
                        let rest: Vec<_> = items.iter()
                            .filter(|w| **w != completed_id)
                            .cloned().collect();
                        let race_result = RaceResult {
                            first: completed_id,
                            value: value.clone(),
                            rest,
                        };
                        let waiter = self.all_waiters.remove(&waiter_id);
                        self.remove_waiter_refs(waiter_id);
                        self.ready_waiters.push_back(ReadyWaiter {
                            task_id: waiter.task_id,
                            continuation: waiter.continuation,
                            result: WaiterResult::Ok(race_result.into()),
                        });
                    }
                    Err(error) => {
                        let waiter = self.all_waiters.remove(&waiter_id);
                        self.remove_waiter_refs(waiter_id);
                        self.ready_waiters.push_back(ReadyWaiter {
                            task_id: waiter.task_id,
                            continuation: waiter.continuation,
                            result: WaiterResult::Err(error.clone()),
                        });
                    }
                }
            }
        }
    }
}
```

### Wake Helper Functions

```rust
/// Returns true if the waitable has reached a terminal state
/// (Completed, Failed, or Cancelled). Used by Gather all-done check.
fn is_completed(&self, w: &Waitable) -> bool {
    match w {
        Waitable::Task(tid) => matches!(
            self.tasks.get(tid),
            Some(TaskState::Completed { .. })
                | Some(TaskState::Failed { .. })
                | Some(TaskState::Cancelled)
        ),
        Waitable::Promise(pid) | Waitable::ExternalPromise(pid) => {
            let map = if matches!(w, Waitable::ExternalPromise(_)) {
                &self.external_promises
            } else {
                &self.promises
            };
            matches!(
                map.get(pid),
                Some(PromiseState::Completed { .. }) | Some(PromiseState::Failed { .. })
            )
        }
    }
}

/// Returns the first error among completed waitables, or None if all succeeded.
/// Cancelled tasks produce TaskCancelledError.
fn first_error_in(&self, items: &[Waitable]) -> Option<PyException> {
    for w in items {
        match w {
            Waitable::Task(tid) => match self.tasks.get(tid) {
                Some(TaskState::Failed { error, .. }) => return Some(error.clone()),
                Some(TaskState::Cancelled) => return Some(TaskCancelledError::new()),
                _ => {}
            },
            Waitable::Promise(pid) | Waitable::ExternalPromise(pid) => {
                let map = if matches!(w, Waitable::ExternalPromise(_)) {
                    &self.external_promises
                } else {
                    &self.promises
                };
                if let Some(PromiseState::Failed { error }) = map.get(pid) {
                    return Some(error.clone());
                }
            }
        }
    }
    None
}

/// Collects Ok values from completed waitables in the original input order.
/// Panics if any waitable is not in Completed state (caller must check first).
fn collect_results_in_input_order(&self, items: &[Waitable]) -> Vec<Value> {
    items.iter().map(|w| {
        match w {
            Waitable::Task(tid) => match self.tasks.get(tid) {
                Some(TaskState::Completed { result, .. }) => result.clone(),
                other => panic!("expected Completed, got {:?}", other),
            },
            Waitable::Promise(pid) | Waitable::ExternalPromise(pid) => {
                let map = if matches!(w, Waitable::ExternalPromise(_)) {
                    &self.external_promises
                } else {
                    &self.promises
                };
                match map.get(pid) {
                    Some(PromiseState::Completed { result }) => result.clone(),
                    other => panic!("expected Completed, got {:?}", other),
                }
            }
        }
    }).collect()
}
```

**Note on `is_completed`**: Cancelled tasks count as "completed" (terminal).
This ensures Gather's all-done check doesn't deadlock when a child is cancelled.
The subsequent `first_error_in` check catches the cancelled task and produces
`TaskCancelledError`, triggering Gather's fail-fast error path.

## Task Completion Flow

### Task Wrapping

When the scheduler creates a task (Spawn), it wraps the program in a
scheduler envelope that handles both preemption and completion notification:

```
original_program
  → _scheduler_envelope(original_program, task_id)   // SchedulerYield + TaskCompleted
  → Call(envelope)                                    // IR expression
```

The envelope catches both success (`StopIteration`) and failure (`Exception`)
from the original program, yielding `Perform(TaskCompleted(task_id, result))`
for both cases. No `FlatMap` wrapper is needed — the envelope handles
everything internally (see §Scheduler Envelope).

### task_id allocation

`task_id` is allocated BEFORE `CreateContinuation`, so the wrapping can
reference the `task_id` in the `TaskCompleted` effect and the `SchedulerYield`
effect.

## Promise vs ExternalPromise

### Promise (doeff-internal)

Both creation and completion happen via effects:

```
doeff task A                     doeff task B
────────────                     ────────────
p = yield CreatePromise()
  ... pass p.future to B ...    yield CompletePromise(p, value)
result = yield Wait(p.future)                ▲
                                             │ effect dispatched
                                             │ to scheduler handler
```

- Created via `CreatePromise` effect
- Completed via `CompletePromise(p, value)` effect
- Failed via `FailPromise(p, error)` effect
- All operations are effects in the normal handler dispatch

### ExternalPromise (outside doeff)

Created via effect, completed via method call:

```
doeff task                       External code (ray worker, thread, I/O)
──────────                       ──────────────────────────────────────
p = yield CreateExternalPromise()
  ... pass p.uuid to external ... complete_by_uuid(p.uuid, value)
result = yield Wait(p.future)              ▲
                                           │ method call → thread-safe queue
                                           │ NOT an effect
```

- `promise.complete(value)` / `promise.fail(error)` — method calls (in-process)
- `promise.uuid` — serializable string for cross-process completion
- Submits to a thread-safe completion queue
- Scheduler drains queue during `switch_to_next` (Priority 3)

| | Promise | ExternalPromise |
|---|---------|-----------------|
| Completed by | doeff code (effect) | external code (method call) |
| Mechanism | Effect dispatch | Thread-safe queue |
| Scheduler learns | Immediately (effect) | On next drain (switch_to_next) |
| Cross-process | No | Yes (via UUID) |

## Terminal vs Non-Terminal Yields

| DoCtrl | Terminal? | Scheduler frame pushed back? |
|--------|-----------|------------------------------|
| `Transfer` | Yes | No — scheduler is done |
| `TransferThrow` | Yes | No — scheduler is done (throws exception) |
| `Resume` | Yes | No |
| `Delegate` | Yes | No |
| `ResumeContinuation` | No | Yes — VM resumes scheduler |
| `CreateContinuation` | No | Yes — VM resumes scheduler |
| `GetHandlers` | No | Yes — VM resumes scheduler |
| `Perform` | No | Yes — VM resumes scheduler |

### ResumeContinuation Subtlety

When the scheduler yields `ResumeContinuation(k_task)` to start a Pending
task, the scheduler's frame IS pushed back (non-terminal). The task runs in
its own segment. When the task yields an effect, a NEW scheduler program is
created via `create_program()`. The old frame is orphaned and cleaned up
when its segment is abandoned.

The scheduler transitions to `SchedulerPhase::Idle` after yielding
`ResumeContinuation`. If resumed unexpectedly, it returns an error.

## SchedulerState

```rust
pub struct SchedulerState {
    /// All tasks.
    tasks: HashMap<TaskId, TaskState>,

    /// FIFO ready queue. Tasks here are Pending or Suspended.
    ready_queue: VecDeque<TaskId>,

    /// Waiters registered on waitables (Task/Promise/ExternalPromise).
    waiters_by_waitable: HashMap<Waitable, Vec<WaiterId>>,
    all_waiters: HashMap<WaiterId, Waiter>,

    /// Waiters ready to be woken (result pre-computed).
    ready_waiters: VecDeque<ReadyWaiter>,

    /// Currently running task (if any).
    current_task: Option<TaskId>,

    /// Tasks with pending cooperative cancellation. Checked at SchedulerYield.
    /// Separate from TaskState because Cancel is issued by another task while
    /// the target may be Running (single-threaded: can't mutate its TaskState).
    cancel_requested: HashSet<TaskId>,

    /// Next task_id to allocate.
    next_task_id: u64,
    next_waiter_id: u64,

    /// Internal promises (created and completed via effects).
    promises: HashMap<PromiseId, PromiseState>,

    /// External promises (created via effect, completed via method call).
    external_promises: HashMap<PromiseId, PromiseState>,

    /// Shared counter for both promise maps (ensures unique IDs across both).
    next_promise_id: u64,

    /// UUID → PromiseId mapping for cross-process ExternalPromise resolution.
    /// External code (ray, multiprocess) sends UUID; completion adapter resolves
    /// to PromiseId for queue submission.
    uuid_to_promise_id: HashMap<String, PromiseId>,

    /// External promise completion queue (thread-safe).
    /// ExternalPromise.complete()/fail() push here from any thread.
    external_completions: Arc<Mutex<VecDeque<ExternalCompletion>>>,
}
```

**No `env_cache`**: `RustStore.env` is shared across all tasks (see §Store
Semantics). The scheduler only swaps `state` and `log` on context switch.

### can_handle

The scheduler handler's `can_handle` method checks whether the given effect
is one of the scheduler's effect types:

```rust
fn can_handle(&self, py: Python, effect: &Bound<'_, PyAny>) -> bool {
    effect.is_instance_of::<PySpawnEffect>()
        || effect.is_instance_of::<PyWaitEffect>()
        || effect.is_instance_of::<PyGatherEffect>()
        || effect.is_instance_of::<PyRaceEffect>()
        || effect.is_instance_of::<PyCancelEffect>()
        || effect.is_instance_of::<PySchedulerYield>()
        || effect.is_instance_of::<PySchedulerTaskCompleted>()
        || effect.is_instance_of::<PyCreatePromiseEffect>()
        || effect.is_instance_of::<PyCompletePromiseEffect>()
        || effect.is_instance_of::<PyFailPromiseEffect>()
        || effect.is_instance_of::<PyCreateExternalPromiseEffect>()
}
```

### remove_waiter_refs

Removes a waiter's registrations from all `waiters_by_waitable` entries.
Called when a waiter is woken (to prevent double-wake from other completions):

```rust
fn remove_waiter_refs(&mut self, waiter_id: WaiterId) {
    // Iterate all waitable→waiter_ids entries and remove this waiter_id
    self.waiters_by_waitable.retain(|_waitable, waiter_ids| {
        waiter_ids.retain(|id| *id != waiter_id);
        !waiter_ids.is_empty()  // remove entry if no waiters left
    });
}
```

### drain_external_promises

Dequeues completions from the thread-safe external completion queue and
wakes any waiters watching those promises:

```rust
fn drain_external_promises(&mut self) {
    let completions: Vec<ExternalCompletion> = {
        let mut queue = self.external_completions.lock().unwrap();
        queue.drain(..).collect()
    };
    for completion in completions {
        let pid = completion.promise_id;
        match completion.result {
            Ok(value) => {
                self.external_promises.insert(pid, PromiseState::Completed { result: value.clone() });
                self.wake_waiters_for(Waitable::ExternalPromise(pid), &Ok(value));
            }
            Err(error) => {
                self.external_promises.insert(pid, PromiseState::Failed { error: error.clone() });
                self.wake_waiters_for(Waitable::ExternalPromise(pid), &Err(error));
            }
        }
    }
}

struct ExternalCompletion {
    promise_id: PromiseId,
    result: Result<Value, PyException>,
}
```

### RustStore access

Rust handlers receive `&mut RustStore` on every `start`/`resume`/`throw`
call (see `RustHandlerProgram` trait in SPEC-008). This is how Get/Put/Tell
handlers work — they read/write `RustStore` directly.

The scheduler uses the same mechanism for context switching:

```rust
impl RustHandlerProgram for SchedulerProgram {
    fn start(&mut self, py: Python, effect: DispatchEffect, k: Continuation,
             store: &mut RustStore) -> RustProgramStep;
    fn resume(&mut self, value: Value, store: &mut RustStore) -> RustProgramStep;
    // ...
}
```

### load_task_store / save_current_task_store

Context switch helpers that transfer state between `TaskStore` and `RustStore`.
Free functions, called from within `start`/`resume` where `&mut RustStore` is
available.

```rust
/// Load a task's isolated state into RustStore (before resuming task).
/// Called with the TaskStore extracted from the TaskState variant.
fn load_task_store(task_store: &TaskStore, store: &mut RustStore) {
    store.state = task_store.state.clone();
    store.log = task_store.log.clone();
    // store.env is NOT touched — shared across all tasks
}

/// Save current RustStore state into a new TaskStore (before switching away).
fn save_current_task_store(store: &RustStore) -> TaskStore {
    TaskStore {
        state: store.state.clone(),
        log: store.log.clone(),
    }
}
```

## Implementation Checklist

### Rust types (`scheduler.rs`)

- [ ] `TaskState` enum with Pending/Running/Suspended/Blocked/Completed/Failed/Cancelled
- [ ] `WaiterKind` enum with Wait/Gather/Race
- [ ] `ReadyWaiter` with `WaiterResult::Ok | Err`
- [ ] `SchedulerState` struct
- [ ] `RaceResult` as `#[pyclass]`
- [ ] `TaskCancelledError` Python exception

### Effect pyclass types (`effect.rs`)

- [ ] `PyWaitEffect` — new
- [ ] `PyCancelEffect` — new
- [ ] `PySchedulerYield` — new (scheduler-internal)
- [ ] Update existing: `PySpawnEffect`, `PyGatherEffect`, `PyRaceEffect`

### Scheduler handler (`scheduler.rs`)

- [ ] Handle `Wait` — block or immediate resolve
- [ ] Handle `Cancel` — set flag, non-blocking return
- [ ] Handle `SchedulerYield` — save/switch with cancel check
- [ ] Handle `Gather` — fail-fast error checking
- [ ] Handle `Race` — return `RaceResult`
- [ ] `switch_to_next` with priority: ready_waiters > ready_queue > drain_external
- [ ] `resume_task` with ResumeContinuation/Transfer dispatch
- [ ] `wake_waiters_for` with typed logic
- [ ] Store isolation: snapshot TaskStore on Spawn, swap state+log on context switch
- [ ] Handle `CreatePromise` — allocate promise, return PyPromise
- [ ] Handle `CompletePromise` — transition promise, wake waiters
- [ ] Handle `FailPromise` — transition promise, wake waiters
- [ ] Handle `CreateExternalPromise` — allocate, return PyExternalPromise

### Python envelope (`__init__.py` or helper module)

- [ ] `_scheduler_envelope(gen, task_id)` wrapper generator
- [ ] Error wrapping in envelope (catch exceptions → TaskCompleted Err)

### Python exports (`lib.rs`)

- [ ] Export `PyWaitEffect`, `PyCancelEffect`, `PyRaceResult`
- [ ] Export `TaskCancelledError` exception
- [ ] Export `PySchedulerYield` (internal, not in `__all__`)

### Python `__init__.py`

- [ ] `WaitEffect = _ext.WaitEffect`
- [ ] `CancelEffect = _ext.CancelEffect`
- [ ] `RaceResult = _ext.RaceResult`
- [ ] `PyWait = WaitEffect` (SPEC-008 names)
- [ ] `PyCancel = CancelEffect`

## Test Plan

### Unit tests (Rust, `scheduler.rs`)

1. `test_resume_task_pending_uses_resume_continuation`
2. `test_resume_task_suspended_uses_transfer`
3. `test_wait_completed_future_returns_immediately`
4. `test_wait_pending_future_blocks_task`
5. `test_wait_failed_future_throws_error`
6. `test_wait_cancelled_future_throws_cancelled_error`
7. `test_gather_all_done_returns_results_in_order`
8. `test_gather_fail_fast_on_first_error`
9. `test_gather_empty_returns_empty_list`
10. `test_race_returns_race_result_with_rest`
11. `test_race_first_error_propagates`
12. `test_cancel_sets_flag_returns_immediately`
13. `test_cancel_at_scheduler_yield`
14. `test_cancel_blocked_task`
15. `test_cancel_pending_task`
16. `test_scheduler_yield_round_robin`
17. `test_switch_to_next_priority_order`
18. `test_store_isolation_on_spawn`
19. `test_env_shared_across_tasks`

### Integration tests (Python, `test_pyvm.py`)

1. `test_spawn_wait_basic` — Spawn task, Wait for result
2. `test_spawn_gather_basic` — Spawn 2 tasks, Gather results
3. `test_spawn_gather_fail_fast` — One task fails, Gather raises
4. `test_spawn_race_returns_race_result` — Race returns RaceResult
5. `test_spawn_race_cancel_losers` — Race + cancel rest
6. `test_cancel_running_task` — Cancel + Wait raises TaskCancelledError
7. `test_interleaving_with_scheduler_yield` — Two tasks interleave
8. `test_store_isolation` — Child and parent stores independent
9. `test_promise_wait` — CreatePromise + CompletePromise + Wait
10. `test_concurrent_kpc_with_scheduler` — ConcurrentKpcHandler via Spawn/Gather
