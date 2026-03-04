# SPEC-TRACE-001: Implementation Notes

These notes document the current traceback assembly architecture in this repository.

---

## Active-chain storage model

Traceback state is stored on `VM` as `trace_state: TraceState` (`packages/doeff-vm-core/src/vm.rs`).
`TraceState` owns one `ActiveChainAssemblyState` (`packages/doeff-vm-core/src/trace_state.rs`)
with:

- `frame_stack`
- `dispatches`
- `frame_dispatch`
- `transfer_targets`
- `dispatch_order`

State is reset per run by `VM::begin_run_session()` via `trace_state.clear()`.

---

## Event application path

VM execution emits transient `CaptureEvent` values through `TraceState::emit_*` helpers.
Each event is immediately applied to `ActiveChainAssemblyState` by:

1. `TraceState::apply_capture_event(...)`
2. `TraceState::apply_active_chain_event(...)`

`CaptureEvent` is not retained as a long-lived chronological log.

---

## Assembly entrypoints

### `assemble_active_chain`

`VM::assemble_active_chain(exception)` delegates to
`TraceState::assemble_active_chain(...)`, which:

1. Clones `active_chain_state`
2. Merges live frame/line data from current segments and visible dispatch snapshots
3. Finalizes unresolved visible dispatches as `Threw` when exception context exists
4. Builds `ActiveChainEntry` rows from frame/dispatch snapshots
5. Deduplicates adjacent identical rows
6. Injects context entries and `ExceptionSite`

### `assemble_traceback_entries`

`VM::assemble_traceback_entries(exception)` returns `TraceEntry` rows for chained/sectioned
rendering using the same incremental state model.

---

## `GetExecutionContext` integration

`GetExecutionContext` dispatches are marked `is_execution_context_effect` and excluded from visible
active-chain output.

When a handler returns `ExecutionContext`, VM calls
`maybe_attach_active_chain_to_execution_context(...)`:

1. Assemble `active_chain` snapshot (`assemble_active_chain(None)`)
2. Append current `ExecutionContext.entries` as `ContextEntry`
3. Set `ExecutionContext.active_chain` to the serialized tuple snapshot

During error enrichment, merged entries are attached to the original exception as
`doeff_execution_context`; `assemble_active_chain(Some(exception))` injects those entries back
into output.

---

## Transfer and spawn notes

### Transfer

- `CaptureEvent::Transferred` stores destination text in `transfer_targets`
- Terminal handler completion reads `transfer_targets` to produce
  `EffectResult::Transferred { target_repr, ... }`
- Pre-transfer chain visibility comes from incremental frame/dispatch state, not log backtracking

### Spawn boundaries

Scheduler propagates spawn metadata (`task_id`, `parent_task`, `spawn_site`) via execution-context
entries (dict payload with `kind == "spawn_boundary"`). Python coercion promotes these to
`SpawnBoundary` active-chain entries (`doeff/trace.py`).

---

## Invariants

- No persisted event-log field in traceback assembly path
- No legacy full-log assembler function in VM core traceback path
- Traceback assembly is on-demand from `ActiveChainAssemblyState`
- Python `format_default()` is render-only and does not reconstruct VM state
