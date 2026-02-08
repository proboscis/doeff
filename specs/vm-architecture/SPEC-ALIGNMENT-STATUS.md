# Spec Alignment Status (Rev9 / Rev11)

Scope checked against:
- `specs/SPEC-TYPES-001-program-effect-separation.md` (Rev9)
- `specs/vm-architecture/SPEC-008-rust-vm.md` (Rev11)

## Closed

- `Yielded::Program` removed from runtime path; DoThunk/generator-like yields route through `DoCtrl::Call`.
- Dispatch pipeline moved to `DispatchEffect` transport (`step.rs`, `vm.rs`, `handler.rs`, `scheduler.rs`, `error.rs`).
- State/Reader/Writer/KPC/Scheduler routing moved to opaque Python effect objects with handler-side decoding.
- Legacy Python runtime fallback removed from `doeff/rust_vm.py`.
- Compatibility aliases removed in `future.py`, `gather.py`, `spawn.py`, `scheduler_internal.py`; `doeff/core.py` removed.

## Remaining / Partial

1. **R11-A (Effect types in Rust as pyclass structs)**
   - Status: **Open**
   - Current runtime effect transport is opaque Python object, but effect classes still primarily come from Python side modules.
   - Scope expanded [Rev 9]: `KleisliProgramCall` (`PyKPC`) is now included as a `#[pyclass(frozen, extends=PyEffectBase)]` struct. Auto-unwrap strategy moves from KPC to KPC handler (handler computes from `kleisli_source` annotations at dispatch time).

2. **R11-C (classify_yielded strictness)**
   - Status: **Partial**
   - Effect path uses `is_effect_object` and no per-effect field extraction.
   - `classify_yielded` still includes a broad `match type_str` block for DoCtrl fallback parsing.

3. **R11-D (Handler trait signatures use Bound/PyAny directly)**
   - Status: **Partial**
   - Traits now use `DispatchEffect` (opaque) instead of typed `Effect` enum for runtime path.
   - Not yet shifted to `&Bound<'_, PyAny>`-first signatures.

4. **R11-E (dispatch fully opaque in all layers)**
   - Status: **Mostly Closed**
   - `DispatchContext.effect`, `Delegate.effect`, `PendingPython` effect payloads are opaque dispatch payloads.
   - Remaining helper conversion scaffolding exists for test compatibility.

7. **Typed test-only effect fixtures (`Effect::{Get,Put,Ask,Tell,Modify}`)**
   - Status: **Intentional test scaffolding**
   - Runtime (`cfg(not(test))`) uses opaque transport only.
   - Test-only variants are retained to keep focused unit tests deterministic
     without requiring Python object construction in every white-box test.

5. **Module-level runtime API contract (`doeff_vm`)**
   - Status: **Open (packaging/runtime validation still needed)**
   - Source exports may not always match installed artifact behavior; deterministic install-time checks still missing.

6. **SPEC-TYPES-001 Phase B/C (DoExpr hierarchy completion)**
   - Status: **Partial**
   - Significant progress on KPC effect-path semantics and map/flat_map behavior.
   - Full type-hierarchy consolidation and annotation strategy cleanup remains.
   - [Rev 9]: KPC promoted to Rust pyclass. `_AutoUnwrapStrategy` to be removed from KPC and moved into KPC handler implementation.
