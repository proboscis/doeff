# SPEC-CORE-005: GetExecutionContext Effect and Active Chain Snapshot

## Status

Draft (rewrite for current runtime architecture)

## 1. Summary

`GetExecutionContext` is a regular effect that flows through the normal handler dispatch path.
It is not a special VM control primitive.

The VM supports two entry paths:

1. On-demand path: user code yields `GetExecutionContext()` directly.
2. On-exception path: the VM synthesizes `GetExecutionContext()` when eligible generator errors are converted.

The result object is `ExecutionContext`. In on-demand flow, the VM post-fills
`ExecutionContext.active_chain` with a snapshot assembled from live runtime state.

## 2. Runtime Types

### 2.1 Rust VM core effect types

`packages/doeff-vm-core/src/effect.rs` defines:

- `PyGetExecutionContext` (`#[pyclass(name = "GetExecutionContext", extends=PyEffectBase)]`)
- `PyExecutionContext` (`#[pyclass(name = "ExecutionContext")]`) with:
  - `entries: list`
  - `active_chain: Optional[Any]`
  - `add(entry)` method for appending context entries

`make_get_execution_context_effect()` creates the dispatchable effect object.

### 2.2 Python API surface

`doeff/effects/execution_context.py` re-exports:

- `GetExecutionContext = doeff_vm.GetExecutionContext`
- `ExecutionContext = doeff_vm.ExecutionContext`

and defines:

- `ActiveChainSnapshot`
- `snapshot_active_chain(context)`

`ActiveChainSnapshot` is the typed Python wrapper used to coerce and render `active_chain`.

## 3. Dispatch Semantics

### 3.1 On-demand path (user-yielded)

Flow:

1. User generator yields `GetExecutionContext()`.
2. Yield classification maps it to `DoCtrl::Perform { effect }`.
3. `VM::handle_yield_effect()` calls `VM::start_dispatch(effect)`.
4. Dispatch proceeds through the ordinary handler chain (`WithHandler` stack + defaults).
5. A handler resumes with an `ExecutionContext` object.
6. During continuation activation, VM calls
   `maybe_attach_active_chain_to_execution_context(...)`.
7. VM assembles current chain via `assemble_active_chain(None)`, merges `entries`, and sets
   `ExecutionContext.active_chain` (tuple payload).
8. The `ExecutionContext` is delivered back to user code.

Key property: this path returns a value, so callers can inspect both
`context.entries` and `context.active_chain`.

### 3.2 On-exception path (VM auto-yielded)

Flow:

1. Program execution raises an eligible exception during VM stepping.
2. `mode_after_generror(...)` synthesizes a `GetExecutionContext` effect and sets:
   - `pending_error_context = original exception`
   - next mode = `HandleYield(DoCtrl::Perform { effect })`
3. `start_dispatch(...)` consumes `pending_error_context` into
   `DispatchContext.original_exception`.
4. Handlers run through the same dispatch chain and resume with `ExecutionContext`.
5. Terminal resume in this error dispatch triggers
   `enrich_original_exception_with_context(...)`:
   - validates resumed value is `ExecutionContext`
   - merges new `entries` with any preexisting context entries
   - attaches merged context to the original exception
6. VM throws the enriched original exception.

Key property: this path enriches a thrown exception; it does not return an
`ExecutionContext` value to user code.

## 4. Active Chain Assembly and Attachment

`trace_state.rs` maintains frame-based `TraceState` data (`frame_stack`, per-frame
`dispatch_display`, `trace_dispatches`) plus live-state merges during assembly.
`assemble_active_chain(...)` is the source of the active chain snapshot.

For on-demand `GetExecutionContext`:

- VM assembles `active_chain` after handler resume and before delivery.
- VM writes it to `ExecutionContext.active_chain`.
- No exception site is injected because no exception object is supplied.

For exception-time traceback assembly:

- VM can assemble active chain with an exception present.
- Existing execution-context entries on the exception are injected into the chain.
- Exception-site data can be injected for formatting.

Visibility rule:

- Dispatches for `GetExecutionContext` are marked in trace state and hidden from
  rendered active-chain effect entries (`is_visible_dispatch` excludes them).

## 5. ActiveChainSnapshot Python Type

`ActiveChainSnapshot` (`doeff/effects/execution_context.py`) provides:

- `from_execution_context(context)`:
  - reads `context.active_chain`
  - coerces entries into `ActiveChainEntry` values
- `format_default(exception=None)`:
  - builds a doeff traceback renderer using `active_chain_entries`
  - renders with active-chain mode enabled

This is the stable Python-level adapter for consumers that want formatted output
without reimplementing conversion logic.

## 6. Invariants

1. `GetExecutionContext` always dispatches through normal effect handling.
2. On-demand path populates and returns `ExecutionContext.active_chain`.
3. On-exception path enriches and rethrows the original exception.
4. `ExecutionContext.entries` are merged cumulatively across enrichment passes.
5. Active-chain rendering omits the `GetExecutionContext` dispatch itself.
