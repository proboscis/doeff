# SPEC-CORE-004: VM Active-Chain Assembly Model

## 1. Overview

This spec defines how `packages/doeff-vm-core` constructs traceback-relevant data.

The VM keeps incremental active-chain state in memory during execution, then assembles a snapshot
on demand through `TraceState::assemble_active_chain(...)`.

Primary implementation files:

- `packages/doeff-vm-core/src/trace_state.rs`
- `packages/doeff-vm-core/src/capture.rs`
- `packages/doeff-vm-core/src/vm.rs`

## 2. Runtime Data Model

### 2.1 VM Ownership

`VM` owns a single `trace_state: TraceState`.

`TraceState` owns:

- `active_chain_state: ActiveChainAssemblyState`

### 2.2 ActiveChainAssemblyState

`ActiveChainAssemblyState` tracks the incremental state used for snapshot assembly:

- `frame_stack: Vec<ActiveChainFrameState>`
- `dispatches: HashMap<DispatchId, ActiveChainDispatchState>`
- `frame_dispatch: HashMap<FrameId, DispatchId>`
- `transfer_targets: HashMap<DispatchId, String>`
- `dispatch_order: Vec<DispatchId>`

This state is mutable during execution and reset at run boundaries (`VM::begin_run_session` calls
`trace_state.clear()`).

### 2.3 Entry Output Types

Final active-chain snapshots are `Vec<ActiveChainEntry>` where each entry is one of:

- `ProgramYield`
- `EffectYield`
- `ContextEntry`
- `ExceptionSite`

Related action/status types:

- `EffectResult`
- `HandlerDispatchEntry`
- `HandlerStatus`
- `HandlerAction`

## 3. Incremental Update Path

### 3.1 Event Emitters

The VM emits trace updates through `TraceState` methods:

- `emit_frame_entered`
- `emit_frame_exited`
- `emit_dispatch_started`
- `emit_delegated`
- `emit_passed`
- `emit_handler_completed`
- `emit_handler_threw_for_dispatch`
- `emit_resume_event`

### 3.2 Transient CaptureEvent Role

Emitters currently construct a transient `CaptureEvent` and immediately apply it with:

- `TraceState::apply_capture_event`
- `TraceState::apply_active_chain_event`

The event is not persisted; it is used as an internal update carrier for
`ActiveChainAssemblyState`.

### 3.3 Dispatch Visibility Rule

Dispatches marked `is_execution_context_effect` are tracked but hidden from user-visible
active-chain rows (`is_visible_dispatch` returns `false` for those dispatches).

## 4. On-Demand Assembly

`TraceState::assemble_active_chain(exception, segments, current_segment, dispatch_stack)` performs:

1. Clone current `active_chain_state`.
2. Merge live frame lines from segment chain and current visible dispatch snapshot.
3. If `exception` is present, finalize unresolved dispatches as `EffectResult::Threw`.
4. Build rows from frame/dispatch state.
5. Deduplicate adjacent equivalent rows.
6. Inject execution-context entries and terminal exception site data.

Pseudo-flow:

```rust
let mut state = active_chain_state.clone();
merge_live_frame_state(&mut state, segments, current_segment, dispatch_stack);
if let Some(exc) = exception {
    finalize_unresolved_dispatches_as_threw(&mut state, exc);
}
let entries = entries_from_active_chain_state(&state, dispatch_stack);
let entries = dedup_adjacent(entries);
inject_context(entries, exception)
```

## 5. Assembly Semantics

### 5.1 Program Rows

`ProgramYield` rows come from `state.frame_stack` with source-line refresh from current stream
debug locations when available.

### 5.2 Effect Rows

`EffectYield` rows come from visible dispatches, carrying:

- effect repr
- per-handler stack snapshots with status updates
- terminal/active effect result

### 5.3 Fallback Behavior

If no rows are produced from the live frame stack, assembly falls back to the newest visible
dispatch plus its continuation snapshot (`snapshot_frames_for_dispatch`).

### 5.4 Exception-Side Completion

When assembling with an exception, unresolved active dispatches are converted to
`EffectResult::Threw`, marking the active/pending handler as `HandlerStatus::Threw`.

### 5.5 Context and Exception Rows

`inject_context(...)` appends:

- `ContextEntry` values from exception-attached execution context entries
- `ExceptionSite` with final exception location/type/message (unless suppressed by the
  suppression rule in `inject_context`)

## 6. GetExecutionContext Integration

### 6.1 Effect Detection

`VM::is_execution_context_effect` detects `PyGetExecutionContext` dispatches. These are marked in
`DispatchContext.is_execution_context_effect` and propagated into `TraceState`.

### 6.2 Context Capture During Error Conversion

When error conversion is allowed, `VM::mode_after_generror` yields `DoCtrl::Perform` with
`make_get_execution_context_effect()` and stores the pending exception on the current segment.

When that dispatch resolves terminally, `TraceState::enrich_original_exception_with_context`
merges returned `ExecutionContext.entries` with any existing context entries and reattaches them to
the original exception.

### 6.3 Active Chain Attachment for Explicit Context Requests

For explicit context requests (no original exception), VM attaches active-chain data to the
returned `ExecutionContext` via `maybe_attach_active_chain_to_execution_context`:

1. `assemble_active_chain(None)` snapshot.
2. Append existing `ExecutionContext.entries` as `ContextEntry` rows.
3. Serialize via `Value::ActiveChain`.
4. Set `ExecutionContext.active_chain` to the produced tuple.

### 6.4 Visibility Contract

`GetExecutionContext` dispatch rows do not appear as user-visible effect rows in assembled
active-chain output.

## 7. Traceback Output Relationship

`TraceState::assemble_traceback_entries(...)` uses the same incremental state and live merge
strategy to produce `Vec<TraceEntry>`. Active-chain output and traceback-entry output share the same
source state, but with different entry types.

## 8. Invariants

1. Snapshot assembly is on demand from incremental state, not from replaying a persisted event
   sequence.
2. Dispatch status transitions are represented in handler-stack status and `EffectResult`.
3. Segment/debug-location reconciliation happens during assembly, so source lines reflect current
   stream location where possible.
4. Execution-context dispatches are tracked for correctness but filtered from visible active-chain
   effect rows.
