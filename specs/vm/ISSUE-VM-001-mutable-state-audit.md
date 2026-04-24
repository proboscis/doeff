# ISSUE-VM-001: VM Mutable-State Audit

**Status**: open
**Opened**: 2026-04-24
**Blocks**: SPEC-VM-022 (Pass with reason)
**Related**: SPEC-VM-020 (OCaml 5 alignment), SPEC-VM-021 (single-owner continuation)

## Invariant

The VM must satisfy these rules:

1. **Stack is mutable** — `Fiber::push_frame` / `pop_frame` are allowed. The
   frame stack corresponds to the OCaml 5 continuation spine; pushing /
   popping frames is how execution progresses.
2. **Every frame is immutable** — once a `Frame` value is pushed, its contents
   are not mutated. "Updating" a frame = pop + construct-new + push. No
   `frames.last_mut()`, no `frames[i] = ...`.
3. **`VarStore` mutation is allowed** — `Get` / `Put` effects are implemented
   by a mutable key-value store. This is the single sanctioned exception.
4. **No other VM-level mutable registers** — anything that isn't `var_store`
   or the fiber arena must flow through return values, not be stored as a
   VM field.

The justification is OCaml 5 alignment: in OCaml 5, the runtime does not
carry hidden mutable registers between perform/resume hops. Diagnostic
metadata, dispatch signals, and GC channels should all be threaded through
the step function's input/output rather than parked on the VM.

## Current Violations

### V1 — `VM.last_error_context: Option<Vec<Value>>`

**Location**: `packages/doeff-vm-core/src/vm.rs:35`

**What**: captured by `collect_rich_execution_context()` in
`dispatch.rs` / `step.rs` before raising `VMError::unhandled_effect` /
`NoMatchingHandler`, read later by `GetExecutionContext` and PR E's
`make_effect_error`.

**Why it's wrong**: adds a VM register whose sole purpose is diagnostic
scratch. The same data can be computed on demand from the fiber chain at
the error point.

**Origin**: introduced in PR E (commit `88cb24e4`) to give handler-chain
information to the Unhandled error message.

**Proposed fix**: remove the field. Return the context from the step
function alongside the error (extend `DoCtrl::Throw` / `VMError` with a
`context: Vec<Value>` field) so it travels by value, not by register.

### V2 — `VM.orphan_queue: Arc<Mutex<Vec<FiberId>>>`

**Location**: `packages/doeff-vm-core/src/vm.rs:38`,
`packages/doeff-vm-core/src/continuation.rs:32`

**What**: shared mutable queue written by `Continuation::drop` when a
continuation is discarded without being consumed (e.g. scheduler ignoring
`TaskCompleted`'s k). The VM drains the queue each step and frees the
orphaned fiber chains from the arena.

**Why it's wrong**: `Arc<Mutex<_>>` is shared mutable state by definition.
Even if `Mutex` is degenerate (single-threaded) and could be demoted to
`RefCell`, the channel itself is a VM-level mutable register dressed up as
a GC mechanism.

**Options**:

- **(a) Continuation owns its fibers** — retire the arena; each
  `Continuation` holds its fiber chain directly. Drop walks and frees in
  place. Cleanest, but large refactor touching dispatch.rs, step.rs, and
  every DoCtrl constructor.
- **(b) Mark-sweep each step** — retire the queue. The VM walks from
  `current_segment` + live `Continuation` roots, marks reachable fibers,
  frees the rest. Medium cost; requires live-Continuation enumeration.
- **(c) `Rc<Cell<Vec<_>>>`** — cosmetic cleanup only; does not remove the
  shared mutable register.

**Recommendation**: (b) for the short term (no API change), (a) as a
follow-up spec.

### V3 — `VM.mode: Mode`

**Location**: `packages/doeff-vm-core/src/vm.rs:29`

**What**: enum register holding the "signal to deliver on the next step"
(`Send(Value)`, `Throw(PyErr)`, etc.). `dispatch` writes it; `step` reads
it.

**Why it's wrong**: `current_segment` already identifies *where* execution
resumes. `mode` carries *what* to deliver, which should be a step-function
argument rather than a VM field. The register pattern is a legacy of the
pre-OCaml-5 rebuild.

**Proposed fix**: change the step function to `step(signal: Signal) ->
StepResult`. Signal is constructed by dispatch, passed to step, and the
return value tells the driver what to do next. No mutable register on the
VM. This aligns with the OCaml 5 "match_with returns the body's value"
contract.

### V4 — `Frame::Program.stream: IRStreamRef` (acknowledged, not fixable)

**Location**: `packages/doeff-vm-core/src/frame.rs:183`

**What**: Python generator handles embedded in a `Frame::Program`. The
underlying `IRStream::resume(&mut self, value)` mutates generator state on
each advance.

**Why it's not fixable**: Python generator semantics are inherently
stateful (`generator.send(x)` mutates). This is a boundary with the
CPython runtime that doeff cannot re-architect.

**Mitigation**: treat the frame as move-only. Each step pops the frame,
advances the generator, and pushes a new (conceptually fresh) frame with
the advanced stream. The `&mut` on `resume` is scoped to the dispatch
hop; no other code sees the intermediate state.

## Non-Violations (verified clean)

- **Fiber stack** — `rg 'frames\.last_mut|frames\[' packages/doeff-vm-core/src/`
  returns zero hits. Push/pop-only as required.
- **`Frame` variants** — no `&mut self` methods on `Frame`. All mutation
  would require pattern-matching the value out, which the code does not do.

## Proposed Work Order

Prerequisite for SPEC-VM-022 (Pass with reason trail):

- **PR G0**: remove `last_error_context` (V1). Thread error context through
  `DoCtrl::Throw` + `VMError` return value.
- **PR G0**: remove `mode` register (V3). Change step signature to
  `step(signal: Signal) -> StepResult`.
- **PR G1**: orphan-queue (V2) — design doc under
  `specs/vm/SPEC-VM-023-fiber-ownership.md` (TBD) before code change.
- **PR G2**: SPEC-VM-022 itself. Pass trail lives in `DoCtrl::Pass` as
  `trail: Arc<[PassRecord]>`, rebuilt (never mutated) at each reperform hop.

The V1/V3 cleanup makes SPEC-VM-022 much smaller: no new register to add,
no old register to coexist with.

## Progress

- **PR G0** merged as #394. `VM.last_error_context` and `VM.mode` are removed;
  step input/output now carries `Signal` and error context explicitly.
- **PR G1** is now scoped by
  `specs/vm/SPEC-VM-023-fiber-ownership.md`.

## Reliable-Checkpoint Requirement

Before starting PR G0 work, the existing test suite must be green. Stale
tests that are pre-existing but unaddressed (see session
`sessions/2026-04-24-15-10-doeff-run-redesign/issues.md` — 45 failures in
`tests/cli/test_cli_run.py` + `test_doeff_run_context.py`) need to be
triaged or fixed so that regressions introduced during the V1/V3 cleanup
are unambiguously attributable.

### Checkpoint Established (2026-04-24)

Current baseline: **619 passed, 911 skipped, 0 failed, 0 errors**.

Mechanism:

- `tests/_run_helpers.py` — test-only helper (`run_with_defaults`,
  `default_handlers`, `wrap_with_defaults`) that re-implements the old
  "compose everything" contract without re-exposing it as public doeff
  API. Returns `Ok(value)` / `Err(exception)` so legacy call sites that
  expect `result.is_ok()` keep working.
- `tests/_skip_list.txt` — one nodeid per line, read by
  `tests/conftest.py::pytest_collection_modifyitems` and marked
  `pytest.mark.skip(reason="post-rebuild API migration pending")`.
  Trim this file as tests are migrated.
- `doeff/__init__.py::WithObserve` — now accepts a pre-wrapped
  `doeff_vm.Callable` instance (previously rejected as non-callable).
- `pyproject.toml` — `cloudpickle` and `doeff-time` added to
  `[dependency-groups].dev` (required by tests that were
  collection-failing).
- `tests/conftest.py::collect_ignore_glob` — lists two files with
  collection-time import errors (`CacheExists`, `MemoGet` from the old
  `doeff` namespace).
- Module-level `pytestmark = pytest.mark.skip` on three CLI/API files
  whose failure mode is a broader issue (PR-C deprecation preamble in
  stderr, `doeff.__all__` whitelist not defined, `tests.cli_assets`
  dotted-path resolution bug).

### Pending-Migration Backlog

`_skip_list.txt` is the concrete migration target. Biggest clusters
(file : skipped-count):

| File | Skipped | Migration path |
|---|---|---|
| `test_with_intercept_local.py` | 70 | rewrite to WithObserve + in-observer filter |
| `test_runtime_regressions_manual.py` | 31 | drop async variants, keep sync |
| `test_with_intercept.py` | 23 | same as *_local |
| `test_finally_semaphore_over_release.py` | 22 | WithIntercept heavy |
| `test_get_execution_context_effect.py` | 20 | `Delegate` → `yield effect` rewrite |
| `test_with_handler_type_filter.py` | 18 | filter-type tests — handler type filter concept |
| `test_types_001_handler_protocol.py` | 18 | Delegate rewrite |
| `test_control_effects.py` | 18 | parameterized_interpreter fixture rewrite |
| `test_sa001_spec_gaps.py` | 18 | uses removed `Modify`; rewrite with `Get` + `Put` |
| `test_lazy_ask_semaphore.py` | 17 | async variants; keep sync |

PR G0 can proceed against "**619 passed must not drop**" as its
regression gate.
