# Documentation Update Checklist

This branch has applied the high-priority documentation fixes that were identified during the audit.

| Area | Status | Notes |
| --- | --- | --- |
| Public API cleanup | Done | Removed current-doc claims for `Safe`, `RuntimeResult`, `run_program()`, and `ProgramRunResult`. |
| Removed `IO(...)` effect | Done | Replaced the old `IO` chapter with a migration note and removed `IO` from reader-facing learning paths and examples. |
| Handler composition shape | Done | Reader-facing docs now use direct `handler(program)` calls and reserve the compatibility shim for deprecated usage. |
| `Pass()` vs `Delegate()` | Done | Transparent fallthrough examples now use `Pass()`. `Delegate()` is only described as the outer-result path. |
| Handler type filtering | Done | Added guidance that the raw handler's effect annotation becomes a runtime type filter inside the installed handler scope. |
| Runner guidance | Done | Reframed `handlers=` on `run()` / `async_run()` as a low-level runner hook rather than the primary custom-composition API. |
| Async cancellation wording | Done | Updated async docs to document `task.cancel()` instead of a fake top-level `Cancel(task)` constructor. |
| High-traffic examples | Done | Replaced deprecated handler-shim examples, stale handler guidance, and outdated snippets in README and the main guides. |
