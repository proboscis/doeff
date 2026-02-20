# SPEC-TRACE-001: Implementation Notes

These are implementation-level notes for SPEC-TRACE-001. The spec itself defines **what** the traceback should look like; this document captures **how** to get there.

---

## Effect creation site extraction (generic protocol)

Every Python effect object has `created_at: EffectCreationContext` set by `create_effect_with_trace()`. The Rust VM should read this at dispatch time:

**In `start_dispatch()` (vm.rs):**
1. Read `created_at` from the dispatched Python effect object via `getattr(effect, "created_at")`
2. If present, extract `filename`, `line`, `function` from the `EffectCreationContext`
3. Store as `Option<EffectCreationSite>` in `CaptureEvent::DispatchStarted` alongside `effect_repr` and handler chain snapshot

**In scheduler (scheduler.rs):**
1. When handling `SchedulerEffect::Spawn`, read the creation site from the dispatch context (not from continuation frames)
2. Store directly as `TaskMetadata.spawn_site`
3. Delete `spawn_site_from_continuation` entirely — no frame walking, no position-based skipping

This replaces the current pattern where the scheduler walks `k.frames_snapshot` and skips the first frame hoping it's the wrapper. The effect object already knows where it was created.

---

## Spawn boundary insertion (structural, not name-based)

The current `is_spawn_boundary_insertion_point` checks `handler_name == SCHEDULER_HANDLER_NAME` to decide where to insert `── in task N ──` separators. This is hardcoded name matching.

**Fix**: Spawn boundaries carry their own metadata (`task_id`, `parent_task`, `spawn_site`). The insertion logic should use this structural data — not handler names — to determine position. The boundary knows which task it belongs to; the active chain entries know which dispatch they came from. Match by dispatch/task relationship, not by handler name string.

Delete the `SCHEDULER_HANDLER_NAME` constant import in `vm.rs`. The scheduler's name is its own business — the VM's traceback assembly should not know or care what the scheduler is called.

---

## `@do` wrapper inner generator access

The current implementation stores the inner generator as a local variable `_doeff_inner = gen` in `do.py:45` and the Rust VM reads it via `f_locals.get_item("_doeff_inner")`. This is fragile — it reaches into Python locals by magic string name.

**Fix**:
1. In `do.py`: Set the inner generator as an **attribute on the wrapper generator object**: `wrapper_gen.__doeff_inner__ = gen` (after creating the wrapper generator, before yielding)
2. In `vm.rs` (`generator_current_line`): Read via `generator.getattr("__doeff_inner__")` instead of `gi_frame.f_locals.get_item("_doeff_inner")`
3. In `scheduler.rs` (`generator_current_line` helper): Same change

This keeps the cross-language contract but makes it explicit — the attribute lives on the generator object itself, not buried in stack frame locals. It's visible via `dir()`, survives frame changes, and doesn't require the generator to be actively executing.

---

## Strict validation in trace.py coercion functions

The `_coerce_handler_status`, `_coerce_handler_kind`, `_coerce_dispatch_action`, and `_coerce_effect_result` functions in `trace.py` currently return silent defaults for unknown values (e.g., unknown status → `"active"`). This masks bugs in the Rust→Python data pipeline.

**Fix**: Raise `ValueError` on unknown values instead of returning defaults. If Rust sends an unrecognized status string, that's a bug that should surface immediately, not be silently coerced.

---

## What changes in Rust VM

1. `supplement_with_live_state` already reads live `f_lineno` for active frames — this becomes the primary source of line numbers
2. Handler stack snapshot per dispatch — when `DispatchStarted` is recorded, also record the full handler stack names at that point
3. Effect repr — use Python `repr()` on the effect object, not the default `<builtins.X object at 0x...>`
4. **Effect creation site** — read `created_at` from every dispatched Python effect and store in dispatch context (see above)
5. **Scheduler: switch from Resume to Transfer for task switches** — `jump_to_continuation` should emit `DoCtrl::Transfer` (not `DoCtrl::Resume`) for started continuations during task switching. This prevents unbounded segment chain growth during cooperative scheduling. `Resume` chains segments via `caller: self.current_segment`; `Transfer` severs with `caller: None`.
6. **Spawn chain tracking** — add `parent_task: Option<TaskId>` and spawn-site metadata to task state. Spawn site comes from the effect's `created_at` (see above), not from frame walking.
7. **Task error trace capture** — when a spawned task fails, assemble its trace and attach it to the exception before storing in `TaskState::Done { result: Err(...) }`. This ensures the task's trace survives propagation through Gather/Wait/Race.

## What changes in Python projection

1. New `format_default()` method on `DoeffTraceback` replacing `format_chained()` as the stderr output
2. Frame selection: select active call chain only, not full chronological log
3. Handler stack rendering with markers
4. **Transfer chain reconstruction** — when a Transfer event is found in capture_log, include pre-transfer frames in the trace (not reachable from live segment walk due to `caller: None`)
5. **Spawn chain rendering** — when an exception carries a task trace (from a spawned task), render the spawn chain with `── in task N (spawned at ...) ──` separators between parent and child trace sections

## What does NOT change

- `format_chained()` — kept as-is for full chronological debug view
- `format_sectioned()` — kept as-is for structured summary
- `format_short()` — kept as-is for one-liner logs
- Capture model (SPEC-CORE-004) — only additive changes (handler stack snapshot per dispatch, effect creation site, spawn-site tracking)
- `__doeff_traceback_data__` attachment mechanism unchanged
