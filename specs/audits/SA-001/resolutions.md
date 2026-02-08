# SA-001: Gap Resolutions

**Instructions:** For each gap, choose a resolution:
- **fix-code**: Fix the implementation to match the spec
- **fix-spec**: Update the spec to match the implementation
- **fix-both**: Update both (e.g., meet in the middle)
- **defer**: Acknowledge but defer to a later milestone
- **wontfix**: Intentional divergence, document and close

---

## Critical Gaps

| ID | Gap | Resolution | Notes |
|----|-----|------------|-------|
| SA-001-G01 | run() defaults to default_handlers() | fix-code | Change default to empty; user must pass handlers explicitly |
| SA-001-G02 | RunResult missing raw_store | already-fixed | Tests pass on current code |
| SA-001-G03 | Modify returns new_value not old_value | fix-code | Rust handler returns old before apply |
| SA-001-G04 | doeff.presets module missing | fix-code | Create doeff/presets.py with sync_preset, async_preset |
| SA-001-G05 | RunResult missing .error property | already-fixed | Tests pass on current code |

## Moderate Gaps

| ID | Gap | Resolution | Notes |
|----|-----|------------|-------|
| SA-001-G06 | No #[pyclass] effect structs (R11-A) | fix-code | Add PyGet, PyPut, etc. pyclass structs |
| SA-001-G07 | Rust bases not wired to Python (R11-F) | fix-code | Export PyEffectBase from doeff_vm |
| SA-001-G08 | classify_yielded duck-typing (R11-C) | fix-code | Replace getattr/hasattr with isinstance |
| SA-001-G09 | KPC not Rust #[pyclass] (Rev 9) | fix-code | Add PyKPC pyclass and export |
| SA-001-G10 | auto_unwrap on KPC not handler (Rev 9) | fix-code | Remove field from KPC dataclass |
| SA-001-G11 | DoExpr/DoThunk/DoCtrl = aliases | fix-code | Create distinct type classes |
| SA-001-G12 | EffectBase/KPC wrong base classes | fix-code | EffectBase(DoExpr), KPC(EffectBase) |
| SA-001-G13 | Implicit KPC handler in run() | fix-code | KPC handler must be explicit |
| SA-001-G14 | Scheduler sentinel not Rust-exported | fix-code | Export from Rust doeff_vm |
| SA-001-G15 | Handler trait sigs diverge | fix-code | Add py: Python<'_> to start() |
| SA-001-G16 | DoCtrl pyclasses no extends=Base | fix-code | Add extends=PyDoCtrlBase |
| SA-001-G17 | String annot missing DoThunk/DoExpr | fix-code | Add patterns to recognizer |
| SA-001-G18 | run() signature defaults/types | fix-code | handlers default=[] not None |
| SA-001-G19 | to_generator too permissive | already-fixed | Raw generators rejected (TypeError) |
| SA-001-G20 | TaskCompleted not public export | fix-code | Export from doeff.effects |

## Minor Gaps

| ID | Gap | Resolution | Notes |
|----|-----|------------|-------|
| SA-001-G21 | Effect enum test-only remnant | already-fixed | enum only in cfg(test) |
| SA-001-G22 | PyShared vs Py<PyAny> spec drift | fix-spec | DONE — Added PyShared definition; updated Frame, CallMetadata, Continuation, DispatchContext, HandlerEntry, Handler fields |
| SA-001-G23 | Continuation::create rename | fix-spec | DONE — Updated to create_unstarted in definition and call site |
| SA-001-G24 | PyException enum vs flat struct | fix-spec | DONE — Documented enum with Materialized/RuntimeError/TypeError variants |
| SA-001-G25 | RunResult concrete not Protocol | fix-code | Make Protocol |
| SA-001-G26 | Callback +Sync not in spec | fix-spec | DONE — Added + Sync bound to Callback type |

## Discussion Items

| ID | Item | Resolution | Notes |
|----|------|------------|-------|
| SA-001-Q01 | Python handler always matches | defer | Need design discussion |
| SA-001-Q02 | PythonCall PendingPython slot | defer | Implementation detail |
| SA-001-Q03 | async_run step_once protocol | defer | Async design topic |
| SA-001-Q04 | INV-1 GIL in handler parsing | defer | Performance topic |
