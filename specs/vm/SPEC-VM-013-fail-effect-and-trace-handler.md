# SPEC-VM-013: GetExecutionContext Effect Dispatch

## Status: Draft (Revision 7)

Implementation plan: [IMPL-VM-013.md](IMPL-VM-013.md)

---

## 1. Summary

GetExecutionContext is a VM-synthesized Effect for exception context enrichment. When a Python
generator raises an unhandled exception during stepping, the VM synthesizes
`GetExecutionContext(exception)` and dispatches it through the handler chain. Every handler in the
chain gets an opportunity to enrich the exception with contextual information before it is thrown
back via `gen.throw()`.

The enriched exception propagates through the generator stack normally — user code's `try/except`
blocks catch the enriched version automatically, with no special API needed.

### GetExecutionContext vs Fail (user-space)

**GetExecutionContext** is VM-synthesized: the VM creates and dispatches it when a generator raises.
Handlers enrich the exception with contextual information (spawn chain, request context, etc.). The
enriched exception is thrown back via `gen.throw()`.

**Fail** (not part of this spec) is a potential future user-space effect: user code explicitly yields
`Fail(exc)`. This is ordinary effect dispatch with no special VM machinery. Fail is a regular Python
effect class, opaque to the VM (C4).

Compatibility guarantee: when no handler processes `GetExecutionContext`, behavior is identical to
current behavior — `Mode::Throw(e)` with existing exception propagation.

---

## 2. Design

### 2.1 GetExecutionContext pyclass

GetExecutionContext is a frozen Rust pyclass:

```rust
#[pyclass(frozen, name = "GetExecutionContext")]
pub struct PyGetExecutionContext {
    #[pyo3(get)]
    pub exception: Py<PyAny>,  // the original Python exception
}
```

Opaque to handler matching (C4) — `PythonHandler::can_handle()` returns `true` for it, same as all
effects. The VM-specific behavior is in dispatch entry and exhaustion, not in handler matching.

### 2.2 Enrichment pipeline

GetExecutionContext is dispatched as an enrichment pipeline. Every handler gets an opportunity to
enrich the exception via the non-terminal Delegate chain (SPEC-VM-010):

```
inner_handler              scheduler (outer)           exhaustion
─────────────              ─────────────────           ──────────
receives (GetExecutionContext(exc), k_user)
  yield Delegate() ──────> receives (GetExecutionContext(exc), K_new)
                           enriched_1 = add_spawn_chain(exc)
                           yield Resume(K_new, enriched_1)
  <── gets enriched_1 back from Delegate
  enriched_2 = add_my_context(enriched_1)
  yield Resume(k_user, enriched_2)

  VM: error dispatch completed → Mode::Throw(enriched_2)
  enriched_2 propagates via gen.throw()
```

Each handler:
1. Receives `(GetExecutionContext(exc), k)`
2. Yields `Delegate()` to let outer handlers enrich first
3. Receives the outer handler's enrichment back (non-terminal Delegate)
4. Adds its own context to the enriched exception
5. Yields `Resume(k, enriched_exc)` to pass the fully-enriched exception back

Handler options:
- `Resume(k, enriched_exc)` — return the enriched exception for throwing
- `Delegate()` — re-perform to outer handler; receives enriched exception back (non-terminal)
- `Pass()` — skip this handler (not part of the enrichment chain)
- `GetTraceback(k)` — inspect the full delegation chain before enriching (SPEC-VM-014)

### 2.3 Resume-as-throw

When `Resume(k, enriched_exc)` completes an error dispatch, the VM throws instead of delivering:

| Dispatch type | Resume behavior |
|---|---|
| Normal dispatch | `Resume(k, value)` → `Mode::Deliver(value)` → generator receives via `send()` |
| Error dispatch | `Resume(k, enriched_exc)` → `Mode::Throw(enriched_exc)` → generator receives via `gen.throw()` |

The VM detects error dispatch by checking `DispatchContext.original_exception`. When present, the
Resume'd value is thrown.

Contract: the Resume'd value during error dispatch MUST be a Python exception (`isinstance` of
`BaseException`). Non-exception values produce a VMError.

```python
@do
def my_program():
    try:
        result = yield dangerous_computation()
    except Exception as e:
        # e is enriched — carries spawn chain, request context, etc.
        # No special API needed; enrichment happened before gen.throw()
        log_error(e)
```

### 2.4 Dispatch entry

GetExecutionContext uses a dedicated dispatch entry — `Mode::DispatchError(PyException)` — not the
normal `start_dispatch()` path. This avoids the `can_handle() == true` problem where all Python
handlers match and the fallback path is unreachable.

- `receive_python_result()` sets `Mode::DispatchError(e)` for eligible `GenError` branches.
- `step()` handles `Mode::DispatchError` with a dedicated error-dispatch loop.
- The error-dispatch `DispatchContext` carries `original_exception: Option<PyException>` for
  exhaustion fallback and Resume-as-throw detection.

### 2.5 GenError conversion matrix

Only specific `GenError` branches convert to `Mode::DispatchError`. The matrix:

| GenError source | Convert? | Rationale |
|---|---:|---|
| EvalExpr | YES | user `@do` generator raised |
| CallFuncReturn | YES | Python call result failed |
| ExpandReturn (handler_return=true) | NO | handler itself threw; avoid error-recursion |
| ExpandReturn (handler_return=false) | YES | expanded sub-program failed |
| StepUserGenerator | CONDITIONAL | only when using user continuation stream with active dispatch |
| RustProgramContinuation | NO | Rust handler plumbing path |
| AsyncEscape | NO | async escape plumbing path |
| ASTStreamStep::Throw (Rust handlers) | NO | out of scope; remains direct `Mode::Throw` |

### 2.6 Exhaustion fallback

When no handler resolves GetExecutionContext:

- All handlers Pass → `Mode::Throw(original_exception)`
- Delegate with no outer handler → equivalent to exhaustion → `Mode::Throw(original_exception)`

Fallback is based on pass-chain/delegate-chain exhaustion, not `Err(no_matching_handler)`.

In practice, the scheduler always handles GetExecutionContext and Resumes (even when it has no spawn
chain to add), so exhaustion only occurs when there is no scheduler in the handler chain.

### 2.7 Guards

Before any `GenError → DispatchError` conversion:

1. **Nested-error guard**: If `in_error_dispatch == true`, bypass and `Mode::Throw(e)`.
2. **BaseException guard**: If `isinstance(exc, BaseException) and not isinstance(exc, Exception)`,
   bypass and `Mode::Throw(e)`.

This preserves `KeyboardInterrupt` and `SystemExit` semantics and prevents recursive error
conversion.

### 2.8 Scheduler enrichment

The scheduler handles GetExecutionContext as a Rust handler. Task is not part of the VM (C4), so
there is no `GetTaskMetadata` DoCtrl — the scheduler has direct access to `TaskMetadata`.

When the scheduler receives `GetExecutionContext(exc)`:

1. Look up current task's `TaskMetadata` (parent_task, spawn_site, spawn_dispatch_id).
2. If spawned (has a parent): enrich the exception with spawn chain information via PyO3.
3. Resume with the enriched exception.
4. If no spawn chain to add (root task): Resume with the original exception unchanged — ensuring
   inner handlers that Delegated always receive a value back.

The scheduler always Resumes. It never Passes or Delegates on GetExecutionContext.

### 2.9 Cross-task propagation

When a spawned task fails, the scheduler re-raises the exception in the parent task via
`throw_to_continuation`. This triggers a new `GenError` in the parent's context → fresh
GetExecutionContext dispatch → scheduler enriches with parent's spawn info.

```
Task C raises exc
  → GetExecutionContext(exc) → scheduler enriches: "C spawned at file.py:87"
  → Task C fails with enriched_1

throw_to_continuation in parent Task B
  → GenError → GetExecutionContext(enriched_1) → scheduler enriches: "B spawned at file.py:42"
  → enriched_2 carries BOTH spawn sites
```

Each task boundary naturally triggers a fresh dispatch. `in_error_dispatch` was reset when the
child's dispatch completed. No infinite recursion.

### 2.10 Interaction with GetTraceback (SPEC-VM-014)

Handlers use `GetTraceback(k)` to obtain the full delegation chain traceback for frame-level
context enrichment:

```python
def frame_enrichment_handler(effect, k):
    if isinstance(effect, doeff_vm.GetExecutionContext):
        traceback = yield doeff_vm.GetTraceback(k)
        enriched = yield doeff_vm.Delegate()
        enriched = add_frame_context(enriched, traceback)
        return (yield doeff_vm.Resume(k, enriched))
    yield doeff_vm.Pass()
```

### 2.11 Supersedes EXCEPTION_SPAWN_BOUNDARIES

GetExecutionContext enrichment replaces the global `EXCEPTION_SPAWN_BOUNDARIES` mechanism:

| Current mechanism | Replacement |
|---|---|
| `EXCEPTION_SPAWN_BOUNDARIES` global static | Eliminated — spawn chain carried on exception |
| `annotate_failed_task` spawn storage | Scheduler's GetExecutionContext handler enriches directly |
| `exception_key()` pointer-based keying | Eliminated — no pointer identity needed |
| `take_exception_spawn_boundaries()` | Eliminated — exception carries its own context |
| `spawn_site_from_continuation()` | Scheduler uses GetTraceback(k) (per SPEC-VM-014) |
| `annotate_spawn_boundary_dispatch` | Eliminated — spawn context is on the exception |

Exception pointer identity is no longer a constraint. The global map, its accessors, and the
pointer-based keying are all deleted.

This supersedes SPEC-VM-014 Phase 3 (move EXCEPTION_SPAWN_BOUNDARIES to scheduler instance). The
migration from global to instance is skipped; the mechanism is eliminated entirely.

---

## 3. Semantics

### 3.1 State machine

```text
GenError(e)
  → eligibility check (matrix + guards)
  → Mode::DispatchError(e)
  → handler chain dispatch of GetExecutionContext(e)
      - handler Resume(k, enriched_exc): Mode::Throw(enriched_exc) via gen.throw()
      - handler Delegate(): re-perform to outer handler (non-terminal)
      - handler Pass(): skip to next handler
      - Pass/Delegate chain exhausted: Mode::Throw(original_exception)
```

### 3.2 Enrichment pipeline flow

```text
  handler_A (inner)        handler_B (middle)         scheduler (outer)
  ─────────────────        ──────────────────         ─────────────────
  Delegate() ────────────> Delegate() ──────────────> enrich with spawn chain
                                                      Resume(K_new2, enriched_1)
                           <── enriched_1
                           enrich with request ctx
                           Resume(K_new, enriched_2)
  <── enriched_2
  enrich with local context
  Resume(k_user, enriched_3)

  VM: Mode::Throw(enriched_3) → gen.throw(enriched_3)
  user try/except catches enriched_3 with full context
```

### 3.3 `in_error_dispatch` reset contract

Reset points (exhaustive):

1. **Exhaustion**: all handlers passed/delegated with no resolver → `Mode::Throw(original)` → reset.
2. **Resolution via Resume**: handler Resumes → error dispatch completed → reset.
3. **Delegated resolution**: handler delegates, outer handler resolves → dispatch completes → reset.
4. **Nested throw**: handler itself throws → nested guard prevents re-conversion; reset only when
   this error dispatch completes/unwinds.

### 3.4 Error-exhaustion DispatchContext cleanup

When error dispatch exhausts:

1. Extract `original_exception` from `DispatchContext` BEFORE marking completed.
2. Call `mark_dispatch_completed(dispatch_id)`.
3. `lazy_pop_completed()` pops the completed context from the dispatch stack.

Symmetry: normal dispatch exhaustion → `VMError::delegate_no_outer_handler` (fatal). Error dispatch
exhaustion → `Mode::Throw(original)` (expected fallback, not an error).

### 3.5 Handler examples

Enrichment handler — delegate first, then add context:

```python
def spawn_context_handler(effect, k):
    if isinstance(effect, doeff_vm.GetExecutionContext):
        traceback = yield doeff_vm.GetTraceback(k)
        enriched = yield doeff_vm.Delegate()
        enriched = add_spawn_chain(enriched, traceback)
        return (yield doeff_vm.Resume(k, enriched))
    yield doeff_vm.Pass()
```

Outermost handler — enrich and resume directly:

```python
def request_context_handler(effect, k):
    if isinstance(effect, doeff_vm.GetExecutionContext):
        enriched = add_request_context(effect.exception)
        return (yield doeff_vm.Resume(k, enriched))
    yield doeff_vm.Pass()
```

Diagnostic handler — log and delegate:

```python
def diagnostic_handler(effect, k):
    if isinstance(effect, doeff_vm.GetExecutionContext):
        traceback = yield doeff_vm.GetTraceback(k)
        for hop in reversed(traceback):
            for frame in hop.frames:
                print(f"  at {frame.func_name} ({frame.source_file}:{frame.source_line})",
                      file=sys.stderr)
        return (yield doeff_vm.Delegate())
    yield doeff_vm.Pass()
```

---

## 4. Edge Cases

1. **All handlers pass/delegate**: `Mode::Throw(original_exception)`.
2. **Exception inside error dispatch**: `in_error_dispatch=true` → bypass re-conversion, throw
   directly.
3. **Handler-origin errors** (`ExpandReturn{handler_return=true}`): remain direct throw. Not
   converted.
4. **Rust handler throws** (`ASTStreamStep::Throw`): unchanged direct throw path.
5. **KeyboardInterrupt / SystemExit**: never converted to GetExecutionContext.
6. **Resume with non-exception**: VMError. Resume'd value must be `BaseException` instance.
7. **GetTraceback(k) during error dispatch**: works normally. Continuation `k` is still registered.
8. **Cross-task propagation**: enriched exception re-raised in parent triggers fresh
   GetExecutionContext. `in_error_dispatch` was reset. No infinite recursion.
9. **Delegate with no outer handler**: treated as exhaustion → `Mode::Throw(original_exception)`.
10. **Handler returns without Resuming k**: dispatch completes; error dispatch throws
    `original_exception`.
11. **Multiple concurrent task failures**: each task's dispatch is independent. No shared state.

---

## 5. Acceptance Criteria

### Effect and dispatch
- [ ] `PyGetExecutionContext` Rust pyclass exists with `exception: Py<PyAny>`, exported from module.
- [ ] `Mode::DispatchError(PyException)` exists and is handled in `step()`.
- [ ] `receive_python_result` does not call `start_dispatch` for error conversion.
- [ ] `DispatchContext` carries `original_exception: Option<PyException>`.

### Conversion matrix
- [ ] Conversion matrix implemented exactly as listed in §2.5.
- [ ] `ExpandReturn{handler_return=true}` GenError remains direct throw.
- [ ] `StepUserGenerator` GenError converts only for user-continuation stream with active dispatch.
- [ ] `RustProgramContinuation` and `AsyncEscape` GenError remain direct throw.
- [ ] `ASTStreamStep::Throw` remains direct throw.

### Dispatch behavior
- [ ] All-pass chain falls back to `Mode::Throw(original_exception)`.
- [ ] Delegate with no outer handler → exhaustion fallback (not `delegate_no_outer_handler`).
- [ ] `PythonHandler::can_handle()==true` no longer makes fallback unreachable.
- [ ] Resume during error dispatch → `Mode::Throw(enriched_exc)`, not `Mode::Deliver`.
- [ ] Resume'd value validated as `BaseException` instance.

### Guards
- [ ] `in_error_dispatch` prevents recursive error conversion.
- [ ] `in_error_dispatch` reset covers: exhaustion, resolution, delegated resolution, nested throw.
- [ ] `KeyboardInterrupt` and `SystemExit` bypass error conversion.

### Scheduler
- [ ] Scheduler recognizes `PyGetExecutionContext` and enriches with spawn chain from `TaskMetadata`.
- [ ] Scheduler always Resumes (even with no enrichment), ensuring Delegate chain receives values.
- [ ] Cross-task: enriched exception re-raised in parent triggers fresh dispatch.

### Global state elimination
- [ ] `EXCEPTION_SPAWN_BOUNDARIES` global static deleted.
- [ ] `exception_key()`, `take_exception_spawn_boundaries()` deleted.
- [ ] `annotate_spawn_boundary_dispatch` deleted.
- [ ] Error-exhaustion calls `mark_dispatch_completed` and is cleaned up by `lazy_pop_completed`.

### Handler API
- [ ] Handler examples use only `isinstance` + `Resume`/`Pass`/`Delegate`/`GetTraceback`.
- [ ] `GetTraceback(k)` works during error dispatch.

---

## 6. Related Specs

- `SPEC-VM-014` (Continuation Parent Chain, GetTraceback DoCtrl) — companion spec. Phase 3
  (EXCEPTION_SPAWN_BOUNDARIES migration) is superseded by this spec.
- `SPEC-VM-010` (Non-Terminal Delegate) — enables the enrichment pipeline
- `SPEC-VM-PROTOCOL` — VM invariants (C3: DoCtrl vocabulary, C4: opaque effects, C9: branch on
  DoExpr)

---

## 7. Revision Log

| Date | Author | Changes |
|---|---|---|
| 2026-02-22 | OpenCode | Initial: Fail dispatch only. |
| 2026-02-22 | OpenCode | Added exception identity, StepUserGenerator no-dispatch-id case, fail-exhaustion cleanup. |
| 2026-02-22 | OpenCode | Major: Renamed Fail → Error. VM-synthesized Effect through handler chain. |
| 2026-02-22 | OpenCode | Revision 5: Error is resumable (recovery path). |
| 2026-02-22 | OpenCode | Revision 6: Renamed Error → GetExecutionContext. Enrichment pipeline, Resume-as-throw, scheduler enrichment, cross-task propagation, eliminated EXCEPTION_SPAWN_BOUNDARIES. |
| 2026-02-22 | OpenCode | Revision 7: Purified spec to desired-state. Extracted implementation details to IMPL-VM-013.md. |
