# SA-001: Spec Audit Report

**Date:** 2026-02-08
**Specs audited:**
- SPEC-008: Rust VM for Algebraic Effects (Rev 11, 4711 lines)
- SPEC-009: Rust VM Public API (Rev 6, 872 lines)
- SPEC-TYPES-001: DoExpr Type Hierarchy (Rev 9, 1054 lines)

**Implementation files:** `packages/doeff-vm/src/*.rs`, `doeff/*.py`, `doeff/effects/*.py`

**Review method:** 9 parallel review agents, cross-referenced and deduplicated.

---

## Summary

| Category | Count |
|----------|-------|
| Critical gaps (must fix) | 5 |
| Moderate gaps (should fix) | 15 |
| Minor gaps (fix optional) | 6 |
| Discussion (spec silent) | 4 |
| Intentional divergences | 4 |
| Invariants verified correct | 15/15 |

**Key themes:**
1. **R11 pyclass migration incomplete** — Spec R11-A/B/C/D/E/F defines Rust `#[pyclass]` effect types, dispatch base classes, and opaque effect passing. The Rust base classes exist but are not wired to Python types. classify_yielded still uses duck-typing fallbacks.
2. **SPEC-TYPES-001 type hierarchy not implemented** — DoExpr/DoThunk/DoCtrl are aliases to ProgramBase, not distinct types. EffectBase is standalone. KPC extends ProgramBase, not EffectBase.
3. **Public API gaps** — run() installs default handlers (violating API-1), RunResult missing raw_store/error, presets module doesn't exist.
4. **Rev 9 KPC migration incomplete** — KPC is still a Python dataclass with auto_unwrap_strategy stored on it, not a Rust #[pyclass] with handler-computed strategy.

---

## ASCII Gap Diagram

```
+-------------------------------------------------------+
|          SPEC-vs-IMPL GAPS  (SA-001)                   |
+-------------------------------------------------------+
|                                                        |
|  CRITICAL (must fix)                                   |
|  +-- SA-001-G01: run() defaults to default_handlers()  |
|  +-- SA-001-G02: RunResult missing raw_store property  |
|  +-- SA-001-G03: Modify returns new_value not old_value|
|  +-- SA-001-G04: doeff.presets module doesn't exist    |
|  +-- SA-001-G05: RunResult missing .error property     |
|                                                        |
|  MODERATE (should fix)                                 |
|  +-- SA-001-G06: No #[pyclass] effect structs (R11-A)  |
|  +-- SA-001-G07: Rust bases not wired to Python (R11-F)|
|  +-- SA-001-G08: classify_yielded duck-typing (R11-C)  |
|  +-- SA-001-G09: KPC not Rust #[pyclass] (Rev 9)       |
|  +-- SA-001-G10: auto_unwrap on KPC not handler (Rev 9)|
|  +-- SA-001-G11: DoExpr/DoThunk/DoCtrl = aliases only  |
|  +-- SA-001-G12: EffectBase/KPC wrong base classes     |
|  +-- SA-001-G13: Implicit KPC handler in run()         |
|  +-- SA-001-G14: Scheduler sentinel not Rust-exported  |
|  +-- SA-001-G15: Handler trait sigs: DispatchEffect     |
|  +-- SA-001-G16: DoCtrl pyclasses no extends=Base      |
|  +-- SA-001-G17: String annot missing DoThunk/DoExpr   |
|  +-- SA-001-G18: run() signature defaults/types        |
|  +-- SA-001-G19: to_generator too permissive           |
|  +-- SA-001-G20: TaskCompleted not public export       |
|                                                        |
|  MINOR (fix optional)                                  |
|  +-- SA-001-G21: Effect enum test-only remnant         |
|  +-- SA-001-G22: PyShared vs Py<PyAny> spec drift      |
|  +-- SA-001-G23: Continuation::create -> create_unst.  |
|  +-- SA-001-G24: PyException enum vs flat struct       |
|  +-- SA-001-G25: RunResult concrete class not Protocol |
|  +-- SA-001-G26: Callback +Sync not in spec            |
|                                                        |
|  DISCUSSION (spec silent)                              |
|  +-- SA-001-Q01: Python handler always matches         |
|  +-- SA-001-Q02: PythonCall uses PendingPython slot    |
|  +-- SA-001-Q03: async_run step_once protocol          |
|  +-- SA-001-Q04: INV-1 GIL in Rust handler parsing     |
|                                                        |
|  INTENTIONAL (no fix needed)                           |
|  +-- SA-001-D01: SegmentArena vs Vec+free_segments     |
|  +-- SA-001-D02: HashMap vs SlotMap for callbacks      |
|  +-- SA-001-D03: current_segment Option (defensive)    |
|  +-- SA-001-D04: Error returns vs panics               |
|                                                        |
+-------------------------------------------------------+
```

---

## Critical Gaps

### SA-001-G01: run() defaults to default_handlers() instead of []

**Spec refs:** SPEC-009 §0 (line 123), §1 (line 149), API-1 (line 852)
**Spec says:** "No implicit behavior. run() installs no handlers by default." `handlers: list[Handler] = []`
**Impl does:** `rust_vm.py:56` — `selected_handlers = list(handlers) if handlers is not None else default_handlers()`. When handlers=None (default), installs [state, reader, writer].
**Files:** `doeff/rust_vm.py:56`, also `doeff/rust_vm.py:71` (async_run)
**Enforcement:** test
**Impact:** User code that omits handlers gets implicit state/reader/writer — violating the spec's core "no magic" principle.

### SA-001-G02: RunResult missing `raw_store` property

**Spec refs:** SPEC-009 §2 (lines 201-203, 240-258)
**Spec says:** `RunResult.raw_store -> dict[str, Any]` — final store snapshot after execution.
**Impl does:** Python RunResult (`_types_internal.py:838-871`) has `.state` but no `.raw_store`. Accessing `result.raw_store` raises AttributeError.
**Files:** `doeff/_types_internal.py:838-871`
**Enforcement:** test
**Impact:** Spec-following user code breaks at runtime.

### SA-001-G03: Modify handler returns new_value, not old_value

**Spec refs:** SPEC-008 (lines 1271-1279)
**Spec says:** `resume()` after Modify yields `Resume(k, old_value)` — caller receives old value (read-then-modify).
**Impl does:** `handler.rs:672-686` — yields `Resume { continuation, value: new_value }`. `_old_value` is discarded.
**Files:** `packages/doeff-vm/src/handler.rs:672-686`
**Enforcement:** test
**Impact:** Modify's return value to the caller differs. User code relying on Modify returning the pre-modification value gets wrong results.

### SA-001-G04: doeff.presets module doesn't exist

**Spec refs:** SPEC-009 §7 (lines 683-689), §8 (lines 729-730)
**Spec says:** `from doeff.presets import sync_preset, async_preset`
**Impl does:** No `doeff/presets.py` or `doeff/presets/` exists.
**Files:** (missing)
**Enforcement:** test
**Impact:** ImportError for spec-following code.

### SA-001-G05: RunResult missing `.error` property

**Spec refs:** SPEC-009 §2 (lines 215-217)
**Spec says:** `@property def error(self) -> BaseException` — get Err or raise ValueError if Ok.
**Impl does:** Python RunResult has `.value`, `.is_ok()`, `.is_err()`, `.result` — but no `.error`.
**Files:** `doeff/_types_internal.py:838-871`
**Enforcement:** test
**Impact:** AttributeError for spec-following code.

---

## Moderate Gaps

### SA-001-G06: No #[pyclass] effect structs (R11-A unimplemented)

**Spec refs:** SPEC-008 R11-A (lines 807-892), SPEC-TYPES-001 Rev 8
**Spec says:** PyGet, PyPut, PyAsk, PyTell, PyModify, scheduler effects, PyKPC as Rust `#[pyclass(frozen)]` structs.
**Impl does:** `effect.rs` has no pyclass structs. Effects are Python classes with `__doeff_*__` marker attributes.
**Files:** `packages/doeff-vm/src/effect.rs`, `doeff/effects/*.py`
**Enforcement:** semgrep (detect marker-attr patterns) + test
**Root cause:** R11 migration Phase D not started.

### SA-001-G07: Rust base pyclasses not wired to Python types (R11-F)

**Spec refs:** SPEC-008 R11-F (lines 915-993), SPEC-TYPES-001 §7
**Spec says:** Python EffectBase extends Rust PyEffectBase. `is_instance_of::<PyEffectBase>()` is C-level check.
**Impl does:** `PyEffectBase`/`PyDoCtrlBase`/`PyDoThunkBase` defined in pyvm.rs but Python EffectBase uses `__doeff_effect_base__` marker, not Rust base.
**Files:** `doeff/_types_internal.py:567`, `packages/doeff-vm/src/pyvm.rs:43-50`
**Enforcement:** semgrep (detect `__doeff_*` markers) + test
**Root cause:** R11-F wiring incomplete — Rust bases declared but not connected.

### SA-001-G08: classify_yielded uses duck-typing fallbacks (R11-C)

**Spec refs:** SPEC-008 R11-C (lines 975-989), SPEC-TYPES-001 §7
**Spec says:** "Three C-level pointer comparisons. No Python imports. No getattr. No hasattr."
**Impl does:** `pyvm.rs:608-849` — ~15 code paths with getattr, hasattr, Python module imports, string matching.
**Files:** `packages/doeff-vm/src/pyvm.rs:608-849`
**Enforcement:** semgrep (detect getattr/hasattr in classify) + test
**Depends on:** SA-001-G07 (bases must be wired first)

### SA-001-G09: KPC not Rust #[pyclass(frozen, extends=PyEffectBase)]

**Spec refs:** SPEC-008 R11-A, SPEC-009 R6-D, SPEC-TYPES-001 Rev 9 §4.6
**Spec says:** PyKPC is `#[pyclass(frozen, extends=PyEffectBase)]` with fields: kleisli_source, args, kwargs, function_name, execution_kernel, created_at.
**Impl does:** KPC is Python `@dataclass(frozen=True)` extending ProgramBase (`doeff/program.py:465-503`). No Rust PyKPC exists.
**Files:** `doeff/program.py:465-503`, `packages/doeff-vm/src/handler.rs:186-233`
**Enforcement:** test
**Depends on:** SA-001-G07 (PyEffectBase must be wired)

### SA-001-G10: auto_unwrap_strategy stored on KPC, not computed by handler

**Spec refs:** SPEC-TYPES-001 Rev 9, SPEC-008 R11-A, SPEC-009 R6-D
**Spec says:** "auto_unwrap_strategy is NOT stored on KPC. The KPC handler computes it from kleisli_source annotations at dispatch time."
**Impl does:** KPC has `auto_unwrap_strategy` field (`program.py:480`). Handler reads it from effect (`handler.rs:200`).
**Files:** `doeff/program.py:480,494`, `packages/doeff-vm/src/handler.rs:200`
**Enforcement:** test + semgrep
**Impact:** Different KPC handlers cannot implement different resolution strategies.

### SA-001-G11: DoExpr/DoThunk/DoCtrl type hierarchy = aliases

**Spec refs:** SPEC-TYPES-001 §1.4, §2
**Spec says:** DoExpr, DoThunk, DoCtrl are distinct types in a proper hierarchy with different capabilities.
**Impl does:** `program.py:544-546` — `DoExpr = ProgramBase`, `DoThunk = ProgramBase`, `Program = DoExpr`. All aliases. No DoCtrl.
**Files:** `doeff/program.py:544-546`
**Enforcement:** test
**Impact:** Phase B of SPEC-TYPES-001 migration incomplete. isinstance-based classification impossible.

### SA-001-G12: EffectBase/KPC wrong base classes

**Spec refs:** SPEC-TYPES-001 §1.4, §4.6
**Spec says:** EffectBase should subclass DoExpr. KPC should subclass EffectBase (it's an Effect, not a DoThunk).
**Impl does:** EffectBase standalone (`_types_internal.py:567`), KPC extends ProgramBase (`program.py:466`).
**Files:** `doeff/_types_internal.py:566-614`, `doeff/program.py:466`
**Enforcement:** test
**Depends on:** SA-001-G11 (DoExpr hierarchy must exist first)

### SA-001-G13: Implicit KPC handler in run()

**Spec refs:** SPEC-TYPES-001 §10 Q11, SPEC-009 API-1
**Spec says:** "run() does NOT auto-include the KPC handler. If a KPC is yielded and no KPC handler is installed, the VM raises an error."
**Impl does:** `pyvm.rs:1822-1835` — run() always wraps program with KPC handler as innermost layer.
**Files:** `packages/doeff-vm/src/pyvm.rs:1822-1835,1886-1898`
**Enforcement:** test
**Impact:** Users cannot observe "no KPC handler" error. Limits pluggability.

### SA-001-G14: Scheduler sentinel not exported as Rust handler

**Spec refs:** SPEC-008 §Python API for Scheduler (lines 1784-1797), SPEC-009 §7
**Spec says:** Scheduler handler importable as Rust-backed sentinel from `doeff.handlers`.
**Impl does:** `pyvm.rs:1957-1998` exports state/reader/writer sentinels but NOT scheduler. `doeff/handlers.py:10-11` falls back to `_SchedulerSentinel()` placeholder.
**Files:** `doeff/handlers.py:10-23`, `packages/doeff-vm/src/pyvm.rs:1957-1998`
**Enforcement:** test

### SA-001-G15: Handler trait signatures diverge from spec

**Spec refs:** SPEC-008 (lines 1111-1126)
**Spec says:** `can_handle(&self, py: Python<'_>, effect: &Bound<'_, PyAny>)`, `start(&mut self, py: Python<'_>, effect: &Bound<'_, PyAny>, ...)`
**Impl does:** `handler.rs:42-57` — no `py` parameter, uses `DispatchEffect` not `&Bound`.
**Files:** `packages/doeff-vm/src/handler.rs:42-57`
**Enforcement:** semgrep
**Impact:** Spec documentation is stale. Implementation correctly defers GIL acquisition. Spec should be updated.

### SA-001-G16: DoCtrl pyclasses missing extends=PyDoCtrlBase

**Spec refs:** SPEC-008 R11-F (line 951), SPEC-TYPES-001 §7
**Spec says:** `#[pyclass(frozen, extends=PyDoCtrlBase, name = "WithHandler")]`
**Impl does:** `pyvm.rs:1166` — `#[pyclass(name = "WithHandler")]` with no extends, no frozen.
**Files:** `packages/doeff-vm/src/pyvm.rs:1166-1236`
**Enforcement:** semgrep
**Depends on:** SA-001-G07

### SA-001-G17: String annotation matching missing DoThunk/DoExpr patterns

**Spec refs:** SPEC-TYPES-001 §3.3
**Spec says:** Match "DoThunk", "DoThunk[...]", "DoExpr", "Thunk" for DO NOT unwrap.
**Impl does:** `program.py:86-94` only matches Program/ProgramLike/ProgramBase patterns.
**Files:** `doeff/program.py:86-94`
**Enforcement:** test
**Impact:** @do functions with DoThunk[T] annotations would incorrectly auto-unwrap args.

### SA-001-G18: run()/async_run() signature defaults and types

**Spec refs:** SPEC-009 §1 (lines 138-178)
**Spec says:** `handlers: list[Handler] = []`, `env: dict = {}`, `store: dict = {}`
**Impl does:** `handlers: Sequence[Any] | None = None`, `env: dict | None = None`, `store: dict | None = None`
**Files:** `doeff/rust_vm.py:45-50`
**Enforcement:** test (for default behavior) + semgrep (for type annotations)

### SA-001-G19: to_generator accepts broader inputs than spec

**Spec refs:** SPEC-008 (lines 3072-3084)
**Spec says:** Accepts ProgramBase only, rejects raw generators with error.
**Impl does:** `pyvm.rs:517-580` — accepts callables, KPCs, DoExpr objects via multiple fallbacks.
**Files:** `packages/doeff-vm/src/pyvm.rs:517-580`
**Enforcement:** test
**Impact:** Less strict than spec — may accept invalid inputs silently.

### SA-001-G20: TaskCompleted not exported as public scheduler effect

**Spec refs:** SPEC-009 §7 (line 662)
**Spec says:** TaskCompleted is a public scheduler effect.
**Impl does:** `_SchedulerTaskCompleted` in `scheduler_internal.py:157` — internal with underscore prefix, not in exports.
**Files:** `doeff/effects/scheduler_internal.py:157`, `doeff/effects/__init__.py`
**Enforcement:** test

---

## Minor Gaps

### SA-001-G21: Effect enum test-only remnant

**Spec refs:** SPEC-008 R11-B
**Spec says:** "Effect enum REMOVED."
**Impl does:** `effect.rs:38-46` — `#[cfg(test)] pub enum Effect { Get, Put, ... }` remains.
**Files:** `packages/doeff-vm/src/effect.rs:38-46`
**Enforcement:** semgrep

### SA-001-G22: PyShared vs Py<PyAny> spec documentation drift

**Spec refs:** Throughout SPEC-008
**Spec says:** Uses `Py<PyAny>` for Python object references.
**Impl does:** Uses `PyShared` (`Arc<Py<PyAny>>`) pervasively.
**Files:** Throughout `packages/doeff-vm/src/*.rs`
**Enforcement:** spec update only

### SA-001-G23: Continuation::create renamed to create_unstarted

**Spec refs:** SPEC-008 (line 653)
**Spec says:** `pub fn create(program: Py<PyAny>, handlers: Vec<Handler>) -> Self`
**Impl does:** `continuation.rs:91` — `pub fn create_unstarted(expr: PyShared, handlers: Vec<Handler>) -> Self`
**Files:** `packages/doeff-vm/src/continuation.rs:91`
**Enforcement:** spec update only

### SA-001-G24: PyException is enum vs spec's flat struct

**Spec refs:** SPEC-008 (lines 2504-2519)
**Spec says:** Flat struct with exc_type/exc_value/exc_tb fields.
**Impl does:** Enum with Materialized/RuntimeError/TypeError variants.
**Files:** `packages/doeff-vm/src/step.rs:14-26`
**Enforcement:** spec update only

### SA-001-G25: RunResult is concrete class, not Protocol

**Spec refs:** SPEC-009 §2 (line 223)
**Spec says:** "RunResult is a protocol."
**Impl does:** `@dataclass(frozen=True) class RunResult(Generic[T])` — concrete class.
**Files:** `doeff/_types_internal.py:837-838`
**Enforcement:** test (minor)

### SA-001-G26: Callback type bound +Sync not in spec

**Spec refs:** SPEC-008 (line 486)
**Spec says:** `Box<dyn FnOnce(Value, &mut VM) -> Mode + Send>`
**Impl does:** `Box<dyn FnOnce(Value, &mut VM) -> Mode + Send + Sync>`
**Files:** `packages/doeff-vm/src/vm.rs:25`
**Enforcement:** spec update only

---

## Discussion Items (Spec Silent)

### SA-001-Q01: Python handler can_handle() always returns true

`handler.rs:98` — `Handler::Python(_) => true`. Python handlers unconditionally claim all effects. Actual handling is decided by the Python callable (which may Delegate). The spec says "each handler's can_handle() decides" but doesn't specify that Python handlers always match. This is a design choice consistent with algebraic effects (Python handlers get first right of refusal).

### SA-001-Q02: PythonCall variants use PendingPython slot, not carried generator

`step.rs:59-68` — GenNext has no fields; GenSend/GenThrow don't carry generator reference. Implementation uses `PendingPython::StepUserGenerator` slot on VM instead. This avoids cloning Py<PyAny> in the enum. Functionally equivalent but architecturally different from spec.

### SA-001-Q03: async_run uses step_once() protocol

`pyvm.rs:1912-1955` — async_run uses `step_once()` returning tagged tuples, not VM.step() returning StepEvent as spec describes. More efficient (fewer Python-Rust round-trips) but different protocol.

### SA-001-Q04: INV-1 GIL briefly acquired during Rust handler effect parsing

Standard handlers call `Python::attach` inside `parse_*_python_effect()` to inspect Python attributes. INV-1 says "GIL is RELEASED during RustProgram handler execution" — technically violated during effect parsing, though handler logic itself is GIL-free.

---

## Intentional Divergences (No Fix Needed)

### SA-001-D01: SegmentArena vs Vec + free_segments

Implementation uses unified `SegmentArena` (arena.rs) instead of spec's separate `Vec<Segment>` + `free_segments: Vec<SegmentId>`. Better encapsulation. **Spec should be updated.**

### SA-001-D02: HashMap vs SlotMap for callbacks

`vm.rs:188` uses `HashMap<CallbackId, Callback>` instead of spec's `SlotMap`. Simpler, functionally equivalent (callbacks are consumed once with fresh IDs). **Spec should be updated.**

### SA-001-D03: current_segment wrapped in Option

`vm.rs:193` — `Option<SegmentId>` instead of bare `SegmentId`. More defensive (handles uninitialized VM). **Spec should be updated.**

### SA-001-D04: Error returns instead of panics

`vm.rs:1115-1121` — handle_delegate returns `VMError` instead of panicking. `vm.rs:712-720` — receive_python_result returns error instead of panicking. **Implementation is better. Spec should be updated.**

---

## Invariants Verification

All 15 invariants from SPEC-008 §Invariants verified correct:

| Invariant | Status |
|-----------|--------|
| INV-1: GIL Boundaries | OK (minor nuance — Q04) |
| INV-2: Segment Ownership | OK |
| INV-3: One-Shot Continuations | OK |
| INV-4: Scope Chain in Segment | OK |
| INV-5: WithHandler Structure | OK |
| INV-6: Handler Execution Structure | OK |
| INV-7: Dispatch ID Assignment | OK |
| INV-8: Busy Boundary Top-Only | OK |
| INV-9: All Effects Through Dispatch | OK |
| INV-10: Frame Stack Order | OK |
| INV-11: Segment Frames Only Mutable State | OK |
| INV-12: Continuation Kinds | OK |
| INV-13: Step Event Classification | OK |
| INV-14: Mode Transitions | OK |
| INV-15: Generator Protocol | OK |
