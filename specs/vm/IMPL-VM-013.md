# IMPL-VM-013: Implementation Plan

Companion to [SPEC-VM-013.md](SPEC-VM-013-fail-effect-and-trace-handler.md).

This document inventories current state, describes the phased implementation, and tracks code-level
changes needed to achieve the spec.

---

## 1. Current State Inventory

### 1.1 `can_handle` always-true (oracle issue C1)

`PythonHandler::can_handle()` at `handler.rs:206-208` returns `true` unconditionally.
`find_matching_handler()` at `vm.rs:2504-2517` iterates the handler chain and returns the first
`can_handle=true`. `start_dispatch()` at `vm.rs:2519` calls `find_matching_handler`, returns
`Err(no_matching_handler)` if none found.

For GetExecutionContext, `start_dispatch` will ALWAYS find a handler (the first Python
pass-through), making the `Err(no_matching_handler)` fallback unreachable. This is why
`Mode::DispatchError` uses a dedicated dispatch loop instead of `start_dispatch`.

### 1.2 `receive_python_result` returns `()` (oracle issue C2)

`receive_python_result()` at `vm.rs:2259-2439` returns `()` — it sets `self.mode` but cannot
directly return dispatch events. Error conversion sets `self.mode = Mode::DispatchError(e)` and the
actual dispatch happens in `step()`.

### 1.3 GenError branches (oracle issue I7)

Six `PyCallOutcome::GenError` branches exist in `receive_python_result` at `vm.rs:2259-2439`:

| Branch | Location | Convert? |
|---|---|---:|
| `(EvalExpr, GenError)` | `vm.rs:2275` | YES |
| `(CallFuncReturn, GenError)` | `vm.rs:2291` | YES |
| `(ExpandReturn{handler_return=true}, GenError)` | `vm.rs:2363` | NO |
| `(ExpandReturn{handler_return=false}, GenError)` | `vm.rs:2363` | YES |
| `(StepUserGenerator, GenError)` | `vm.rs:2398` | CONDITIONAL — only when `dispatch_uses_user_continuation_stream(...)` at `vm.rs:2407` is true AND `dispatch_id` is `Some(...)` at `vm.rs:2406`; when `dispatch_id` is `None`, falls through to `Mode::Throw(e)` at `vm.rs:2414` |
| `(RustProgramContinuation, GenError)` | `vm.rs:2421` | NO |
| `(AsyncEscape, GenError)` | `vm.rs:2429` | NO |

Out of scope: `ASTStreamStep::Throw` from Rust handlers at `vm.rs:1869-1880`.

### 1.4 `EXCEPTION_SPAWN_BOUNDARIES` global static

`EXCEPTION_SPAWN_BOUNDARIES` at `scheduler.rs:366-367` is a
`OnceLock<Mutex<HashMap<usize, Vec<ExceptionSpawnBoundary>>>>`.

Related functions:
- `exception_spawn_boundaries()` — global accessor
- `exception_key()` at `scheduler.rs:379-384` — derives key from `exc_value.as_ptr() as usize`
- `take_exception_spawn_boundaries()` at `scheduler.rs:386-394` — drains entries by key
- `annotate_failed_task()` at `scheduler.rs:1337-1364` — writes entries on task failure
- `annotate_spawn_boundary_dispatch()` at `scheduler.rs:226-239` — writes entries on dispatch

VM traceback assembly at `vm.rs:977-989` calls `take_exception_spawn_boundaries(error)`.

### 1.5 Dispatch bookkeeping (preserved)

Existing calls to `mark_dispatch_completed` and `mark_dispatch_threw` in `receive_python_result` at
`vm.rs:2363-2414` stay in place. Error conversion is layered on top. No changes to one-shot
consumption semantics at `vm.rs:2626-2635` and `vm.rs:2707-2844`.

---

## 2. Implementation Phases

### Phase 1: `PyGetExecutionContext`, `Mode::DispatchError`, conversion gates

**driver.rs**:
```rust
pub enum Mode {
    Deliver(Value),
    Throw(PyException),
    HandleYield(DoCtrl),
    DispatchError(PyException),  // NEW
    Return(Value),
}
```

**effect.rs** (or new `error.rs`):
```rust
#[pyclass(frozen, name = "GetExecutionContext")]
pub struct PyGetExecutionContext {
    #[pyo3(get)]
    pub exception: Py<PyAny>,
}

#[pymethods]
impl PyGetExecutionContext {
    #[new]
    fn new(exception: Py<PyAny>) -> Self {
        PyGetExecutionContext { exception }
    }
}
```

**vm.rs**:
```rust
pub struct VM {
    // ...existing fields...
    in_error_dispatch: bool,  // NEW
}

pub fn step(&mut self) -> StepEvent {
    match &self.mode {
        Mode::DispatchError(_) => self.step_dispatch_error(),  // NEW
        Mode::Deliver(_) | Mode::Throw(_) => self.step_deliver_or_throw(),
        Mode::HandleYield(_) => self.step_handle_yield(),
        Mode::Return(_) => self.step_return(),
    }
}

pub fn receive_python_result(&mut self, outcome: PyCallOutcome) {
    match (pending, outcome) {
        (PendingPython::EvalExpr { .. }, PyCallOutcome::GenError(e)) => {
            self.mode = self.mode_after_generror(GenErrorSite::EvalExpr, e);
        }
        // ...apply exact matrix from §1.3...
    }
}

fn mode_after_generror(&mut self, site: GenErrorSite, e: PyException) -> Mode {
    if !site.allows_error_conversion() {
        return Mode::Throw(e);
    }
    if self.in_error_dispatch || self.is_non_exception_base_exception(&e) {
        return Mode::Throw(e);
    }
    Mode::DispatchError(e)
}
```

**dispatch.rs**:
```rust
pub struct DispatchContext {
    // ...existing fields...
    pub original_exception: Option<PyException>,  // NEW: None = normal, Some = error dispatch
}
```

**pyvm.rs**: Export `PyGetExecutionContext` from Python module.

### Phase 2: Error dispatch loop, Resume-as-throw

**vm.rs**:
```rust
fn step_dispatch_error(&mut self) -> StepEvent {
    let exc = take_dispatch_error_exception(&mut self.mode)?;
    self.in_error_dispatch = true;

    let error_effect: DispatchEffect = self.make_get_execution_context_effect(&exc)?;
    let scope_chain = self.current_scope_chain();
    let handler_chain = self.visible_handlers(&scope_chain);

    if handler_chain.is_empty() {
        return self.on_error_dispatch_exhausted(exc);
    }

    let dispatch_id = DispatchId::fresh();
    let (k_user, prompt_seg_id) = self.capture_dispatch_continuation(dispatch_id)?;

    self.dispatch_stack.push(DispatchContext {
        dispatch_id,
        effect: error_effect.clone(),
        handler_chain,
        handler_idx: 0,
        k_user,
        prompt_seg_id,
        completed: false,
        original_exception: Some(exc.clone()),
    });

    self.invoke_next_error_handler_or_throw(dispatch_id)
}

fn on_error_dispatch_exhausted(&mut self, exc: PyException) -> StepEvent {
    self.in_error_dispatch = false;
    self.mode = Mode::Throw(exc);
    StepEvent::Continue
}

// Resume-as-throw: intercept dispatch completion during error dispatch.
// When DispatchContext.original_exception is Some, the Resume'd value is thrown
// via Mode::Throw instead of delivered via Mode::Deliver.
fn on_error_dispatch_completed(&mut self, dispatch_id: DispatchId, resumed_value: Value) {
    self.mark_dispatch_completed(dispatch_id);
    self.in_error_dispatch = false;
    let enriched_exc = self.value_to_exception(resumed_value);
    self.mode = Mode::Throw(enriched_exc);
}
```

**handle_pass / handle_delegate changes**:
- `handle_pass`: if error dispatch reaches chain end → `on_error_dispatch_exhausted(original)`
- `handle_delegate`: if error dispatch has no outer handler → same exhaustion path (do NOT emit
  `delegate_no_outer_handler` for GetExecutionContext)

### Phase 3: Scheduler GetExecutionContext handling

**scheduler.rs**:
```rust
// Add to SchedulerEffect enum
pub enum SchedulerEffect {
    // ... existing variants (Spawn, Gather, Race, etc.) ...
    GetExecutionContext { exception: Py<PyAny> },
}

// Update parse_scheduler_python_effect
fn parse_scheduler_python_effect(
    py: Python,
    effect: &Py<PyAny>,
) -> Option<SchedulerEffect> {
    // ... existing checks for PySpawn, PyGather, PyRace, etc. ...
    if let Ok(gec) = effect.downcast::<PyGetExecutionContext>(py) {
        return Some(SchedulerEffect::GetExecutionContext {
            exception: gec.exception.clone(),
        });
    }
    None
}

// SchedulerProgram handles GetExecutionContext.
// Always Resumes — even with no spawn chain to add.
fn handle_get_execution_context(
    &mut self,
    py: Python,
    exception: Py<PyAny>,
    k: &Continuation,
) -> DoCtrl {
    let task_meta = self.state.task_metadata.get(&self.current_task_id);
    if let Some(meta) = task_meta {
        if meta.parent_task.is_some() {
            let enriched = self.enrich_with_spawn_chain(py, exception, meta);
            return DoCtrl::Resume {
                continuation: k.clone(),
                value: Value::PyObject(enriched),
            };
        }
    }
    // No spawn chain info — resume with original exception unchanged
    DoCtrl::Resume {
        continuation: k.clone(),
        value: Value::PyObject(exception),
    }
}
```

### Phase 4: Delete global EXCEPTION_SPAWN_BOUNDARIES

**Delete from `scheduler.rs`**:
- `EXCEPTION_SPAWN_BOUNDARIES` static at `scheduler.rs:366-367`
- `exception_spawn_boundaries()` accessor
- `exception_key()` helper at `scheduler.rs:379-384`
- `take_exception_spawn_boundaries()` at `scheduler.rs:386-394`
- Spawn boundary storage in `annotate_failed_task` at `scheduler.rs:1337-1364`
- `annotate_spawn_boundary_dispatch` at `scheduler.rs:226-239`

**Update `vm.rs`**: Remove `take_exception_spawn_boundaries` call in traceback assembly at
`vm.rs:977-989`.

The `ExceptionSpawnBoundary` struct (`scheduler.rs:163-169`) may be retained if useful for the
scheduler's internal enrichment, or removed.

---

## 3. Files Changed

| File | Change | Phase |
|---|---|---:|
| `packages/doeff-vm/src/driver.rs` | Add `Mode::DispatchError(PyException)` | 1 |
| `packages/doeff-vm/src/effect.rs` (or new `error.rs`) | Add `PyGetExecutionContext` frozen pyclass | 1 |
| `packages/doeff-vm/src/vm.rs` | `in_error_dispatch`, conversion matrix gates, dedicated error loop, Resume-as-throw, fallback, reset logic | 1-2 |
| `packages/doeff-vm/src/dispatch.rs` | Add `original_exception: Option<PyException>` to `DispatchContext` | 1 |
| `packages/doeff-vm/src/pyvm.rs` | Export `PyGetExecutionContext` from Python module | 1 |
| `packages/doeff-vm/src/scheduler.rs` | `SchedulerEffect::GetExecutionContext`, `parse_scheduler_python_effect`, enrichment handler | 3 |
| `packages/doeff-vm/src/scheduler.rs` | Delete `EXCEPTION_SPAWN_BOUNDARIES`, `exception_key()`, `take_exception_spawn_boundaries()`, `annotate_spawn_boundary_dispatch`; simplify `annotate_failed_task` | 4 |
| `packages/doeff-vm/src/vm.rs` | Remove traceback assembly spawn boundary references | 4 |
| `tests/` | Conversion matrix, guard, fallback, reset contract, scheduler enrichment, cross-task tests | 1-4 |

---

## 4. Oracle Issue Closure

| Issue | Resolution |
|---|---|
| C1 fallback unsound | `Mode::DispatchError` with pass/delegate exhaustion fallback, not `start_dispatch` no-match fallback |
| C2 `receive_python_result` returns `()` | Conversion path only sets mode; dispatch happens in `step()` |
| I7 conversion matrix incomplete | Explicit branch-by-branch table with YES/NO/CONDITIONAL |
| I8 Rust handler throw path | Explicitly out of Phase 1; remains direct `Mode::Throw` |
| I9 nested fail recursion | `in_error_dispatch: bool` guard + reset contract |
| I10 BaseException handling | `BaseException and not Exception` bypass rule |
| N1 no `@effect_handler` API | Examples use plain generator handlers only |
| N4 dispatch bookkeeping | Preserve existing `mark_dispatch_completed` / `mark_dispatch_threw`; error layer is additive |

---

## 5. References

- `packages/doeff-vm/src/handler.rs:206-208` — `PythonHandler.can_handle` always true
- `packages/doeff-vm/src/vm.rs:2259-2439` — `receive_python_result` GenError branches
- `packages/doeff-vm/src/vm.rs:2504-2600` — `find_matching_handler`, `start_dispatch`
- `packages/doeff-vm/src/vm.rs:977-989` — traceback assembly spawn boundary call
- `packages/doeff-vm/src/vm.rs:2363-2414` — dispatch bookkeeping (`mark_dispatch_completed` / `mark_dispatch_threw`)
- `packages/doeff-vm/src/vm.rs:2626-2635` — one-shot consumption semantics
- `packages/doeff-vm/src/vm.rs:1869-1880` — `ASTStreamStep::Throw` (Rust handler throw path)
- `packages/doeff-vm/src/scheduler.rs:163-169` — `ExceptionSpawnBoundary` struct
- `packages/doeff-vm/src/scheduler.rs:226-239` — `annotate_spawn_boundary_dispatch`
- `packages/doeff-vm/src/scheduler.rs:366-367` — `EXCEPTION_SPAWN_BOUNDARIES` global static
- `packages/doeff-vm/src/scheduler.rs:379-384` — `exception_key()`
- `packages/doeff-vm/src/scheduler.rs:386-394` — `take_exception_spawn_boundaries()`
- `packages/doeff-vm/src/scheduler.rs:1337-1364` — `annotate_failed_task`
- `.sisyphus/notepads/fail-effect-trace/learnings.md` — oracle issue inventory
