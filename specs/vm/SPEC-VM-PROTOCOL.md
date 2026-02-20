# SPEC-VM-PROTOCOL: VM↔Python Typed Protocol

## Status: Implemented

Implementation plan: [IMPL-VM-PROTOCOL.md](IMPL-VM-PROTOCOL.md)

---

## 1. The Fundamental Principle: DoCtrl Is Everything

The VM is a stepping engine. Its **only** vocabulary is `DoCtrl`. If the VM must care about something — branch on it, read it, process it — that something must arrive as a DoCtrl primitive or be carried on a typed Rust PyClass that the VM receives through DoCtrl-adjacent channels (generator frames, exception materialization).

This is not a guideline. It is the architectural invariant.

### 1.1 What "the VM cares about" means precisely

The VM cares about something if the VM's `step()` function (or any function it calls) makes a **decision** based on that information. Decisions include:

- **Branching**: `match` / `if` on the information to choose a code path
- **Reading**: extracting a field value to use in computation
- **Writing**: setting a field on a Python object for later consumption

If none of these happen, the VM doesn't care. It's just passing opaque data through.

### 1.2 What the VM does NOT care about

| Category | Why VM doesn't care | Who cares |
|----------|---------------------|-----------|
| **Specific effects** | Effects are opaque `PyShared`. The VM dispatches `DoCtrl::Perform { effect }` to handlers. It never looks inside the effect. | Handlers (user-space) |
| **Handlers** | Handlers are user-space objects. The VM stores them in the handler chain and invokes them. It never examines handler internals beyond `can_handle()`. | Handler implementations |
| **Scheduler semantics** | Spawn, Gather, Race, Cancel, Promise — all handled by the scheduler handler, which is a `RustProgramHandler`. The scheduler is architecturally identical to the state handler or any user Python handler. | Scheduler handler |
| **Effect field structure** | Whether an effect has `key`, `value`, `program`, `items` — irrelevant to the VM. Handlers downcast and read these. | Handler implementations |
| **`@do` decorator details** | How `@do` wraps generators, what bridge_generator does, `_DoGeneratorProxy` — none of this is VM's concern. The VM receives a generator-like object and steps it. | `@do` decorator (Python) |

### 1.3 What the VM DOES care about

These are the exhaustive list of things the VM makes decisions on:

| What | How it arrives | VM decision |
|------|---------------|-------------|
| **DoCtrl variant** | `classify_yielded()` reads tag from `PyDoCtrlBase` | Branch on variant (Pure, Call, Perform, Resume, Transfer, WithHandler, Delegate, etc.) |
| **Generator stepping** | `step_generator()` calls `send()`/`__next__()` on the generator object | Branch on outcome (yield / return / error) |
| **Generator location** | `DoeffGenerator.get_frame` callback (see §3.4) | Used in trace assembly to supplement frame line numbers |
| **Call metadata** | `CallMetadata` on `DoCtrl::Call` (already typed) | Stored on `Frame::PythonGenerator`, emitted in trace events |
| **Exception propagation** | `pyerr_to_exception()` converts `PyErr` → internal `PyException` | Stack unwinding, dispatch completion |
| **Traceback data attachment** | `DoeffTracebackData` PyClass on `RunResult` (see §4) | Output enrichment before returning to Python |
| **Handler chain matching** | `can_handle()` on `Handler::RustProgram` / `Handler::Python` | Finding which handler to dispatch to |
| **Continuation state** | `Continuation` struct (already typed) | Resume, Transfer, dispatch completion |

**Key protocol mechanisms for the two non-trivial items:**

1. **Generator location** — user-provided `get_frame` callback on `DoeffGenerator` (§3). VM never touches generator internals.
2. **Traceback data** — `DoeffTracebackData` PyClass delivered via `RunResult` (§4). VM never sets attributes on exception objects.

All other items are already typed: DoCtrl arrives via tagged PyClasses, CallMetadata is a typed Rust struct, continuations are typed, handler matching uses `can_handle()`.

---

## 2. Design Constraints

These are non-negotiable invariants for all VM↔Python communication:

### C1: Zero Dunder Attributes

No `__doeff_*` attributes shall be read from or written to Python objects by the VM. See [IMPL-VM-PROTOCOL.md §1.1](IMPL-VM-PROTOCOL.md#11-dunder-attribute-violations-spec-c1) for current violations.

### C2: All VM↔Python Data-Bearing Types Are Rust PyClasses

Every object that crosses the VM↔Python boundary carrying data the VM reads or writes must be a `#[pyclass]` with typed fields. No `getattr()` probing, no `hasattr()` checks, no dict key lookups.

**Python handlers**: Handlers are opaque callable endpoints. The VM invokes them but never reads their fields. `Handler::Python` carries typed metadata (handler name, source file, source line) provided at WithHandler registration time — the VM never probes `__code__` or `__name__` from handler objects. Handler identity is tracked via Python `id()` (see §6). See [IMPL-VM-PROTOCOL.md §1.2](IMPL-VM-PROTOCOL.md#12-untyped-python-object-access-spec-c2) for current violations.

### C3: DoCtrl Is the Only VM Instruction Vocabulary

If the VM needs new behavior, it gets a new DoCtrl variant. Not a new dunder. Not a new import. A DoCtrl.

### C4: Effects Are Opaque

The VM holds effects as `PyShared` (opaque). It never downcasts, never reads fields, never checks type beyond what `can_handle()` does. The VM is a dumb pipe for effects.

### C5: Handlers Are User-Space

Handlers are not part of the VM. The scheduler handler, state handler, reader handler, writer handler — these are all `RustProgramHandler` implementations that happen to be in the same Rust crate for performance. The VM invokes them through the same `Handler` enum as Python handlers. No special treatment.

### C6: No Python Module Imports from VM Core

The VM core (stepping engine) must not `import("doeff.*")` anything. If the VM needs to call Python, it does so through:
- `NeedsPython(PythonCall::*)` — the existing async callback mechanism
- PyClass methods defined in Rust
- Never by importing Python modules at runtime

Handler-level imports (e.g., scheduler importing `doeff.effects.spawn` for `TaskCancelledError`) are handler concerns, not VM concerns. They may exist in the same crate but are architecturally separate. See [IMPL-VM-PROTOCOL.md §1.3](IMPL-VM-PROTOCOL.md#13-python-module-import-violations-spec-c6) for current violations.

### C7: No Silent Fallbacks

The VM must never silently degrade. If expected typed data is missing, the VM raises an error with clear diagnostic info identifying the object in question. Specifically:

- No `"<anonymous>"` string as a fallback for missing function names in runtime paths.
- No `"<unknown>"` string as a fallback for missing source files in runtime paths.
- No `CallMetadata::anonymous()` in user-facing runtime paths (acceptable only in Rust-only unit tests and VM-internal synthetic calls where metadata is provided via other typed channels — see §5.5).
- No `unwrap_or(metadata.source_line)` — if live line probing fails, raise with diagnostic info.
- No `__code__` introspection fallback in `call_metadata_from_pycall` — `PyCall.meta` must be present.

See [IMPL-VM-PROTOCOL.md §1.4](IMPL-VM-PROTOCOL.md#14-silent-fallback-violations-spec-c7) for current violations.

### C8: VM Entry Accepts DoExpr

The VM's `run()` / `async_run()` interface accepts all `DoExpr` types (Pure, Call, Map, FlatMap, Perform, WithHandler, etc.). `DoeffGenerator` is not an entry-point type — it is the typed wrapper for generators that appear during DoExpr evaluation.

When a DoExpr evaluates to a Python generator (via `to_generator_strict`, `Call` result, handler invocation), that generator must be a `DoeffGenerator`. The VM does not accept raw Python generators at any frame push site.

---

## 3. DoeffGenerator: Typed Generator Wrapper

### 3.1 Problem

The VM steps Python generators. To build traces, it needs to know where the generator is (function name, file, line). For `@do`-decorated generators, the stepping generator is a bridge generator — its `gi_frame` belongs to the bridge, not the user's function. The VM must never probe generator internals or walk implementation-specific chains to find the "real" frame.

### 3.2 Solution: `DoeffGenerator` Rust PyClass

```rust
#[pyclass(frozen, name = "DoeffGenerator")]
pub struct DoeffGenerator {
    /// The generator to step (bridge_generator for @do, raw generator otherwise)
    #[pyo3(get)]
    generator: Py<PyAny>,

    /// Function name (resolved at construction time, not by VM)
    #[pyo3(get)]
    function_name: String,

    /// Source file path (resolved at construction time)
    #[pyo3(get)]
    source_file: String,

    /// Source line number (initial, resolved at construction time)
    #[pyo3(get)]
    source_line: u32,

    /// User-provided callback: Callable[[generator], Optional[FrameType]]
    /// Returns the Python frame to use for live location info, or None
    /// if unavailable (e.g., generator exhausted). The VM passes the
    /// stepping generator as argument. The callback knows how to find
    /// the meaningful frame (e.g., navigating from bridge to inner
    /// generator's gi_frame for @do).
    #[pyo3(get)]
    get_frame: Py<PyAny>,
}
```

### 3.3 Construction

**Python side constructs DoeffGenerator. The VM never constructs it.**

There is no generic "detect-and-wrap" function. Each construction site knows its own context and provides its own `get_frame` callback.

#### Default callback (plain generators, no bridge):

```python
def _default_get_frame(gen):
    """Return the generator's own frame."""
    return gen.gi_frame  # None if exhausted
```

#### `@do` decorator — bridge generator with callback navigating to inner:

The `@do` bridge generator holds the user's generator as a local variable (`gen`). The callback navigates from the bridge's frame to find the user generator's frame:

```python
def _do_get_frame(bridge_gen):
    """Navigate bridge locals to return user generator's frame."""
    if bridge_gen.gi_frame is not None:
        user_gen = bridge_gen.gi_frame.f_locals.get('gen')
        if user_gen is not None:
            return user_gen.gi_frame  # None if exhausted
    return None
```

No structural changes to `generator_wrapper` are needed. The callback runs at probe time, when the bridge has already started and the user gen exists in its locals.

```python
DoeffGenerator(
    generator=bridge_gen,
    function_name=func.__name__,
    source_file=func.__code__.co_filename,
    source_line=func.__code__.co_firstlineno,
    get_frame=_do_get_frame,
)
```

#### `WithHandler` wrapping — single generator (no bridge):

```python
DoeffGenerator(
    generator=handler_gen,
    function_name=handler_fn.__code__.co_name,
    source_file=handler_fn.__code__.co_filename,
    source_line=handler_fn.__code__.co_firstlineno,
    get_frame=_default_get_frame,
)
```

#### `ProgramBase.to_generator()` — implementor's responsibility:

```python
DoeffGenerator(
    generator=gen,
    function_name=...,
    source_file=...,
    source_line=...,
    get_frame=_default_get_frame,  # or custom
)
```

#### What the frame provides

A Python frame object (`types.FrameType`) gives the consumer everything:

| Field | What it provides |
|-------|-----------------|
| `f_lineno` | Current line number (the live line, updated as generator steps) |
| `f_code.co_filename` | Source file path |
| `f_code.co_name` | Function name |
| `f_code.co_firstlineno` | Function definition line |
| `f_locals` | Local variable state (for debugging) |

This means the trace system can resolve code snippets via `linecache.getline(frame.f_code.co_filename, frame.f_lineno)` — exactly what `_resolve_code` in `traceback.py` does — without needing effect-level `created_at`.

See §3.8 for the complete list of construction sites and their responsibilities.

### 3.4 VM Behavior

When the VM receives a `DoeffGenerator`, it:

1. **Extracts `generator`** for stepping (`send()` / `__next__()` / `throw()`)
2. **Stores `get_frame`** callback on the frame for later invocation
3. **Reads `function_name`, `source_file`, `source_line`** as initial `CallMetadata` for the frame

When the VM needs live location info (trace assembly, resume location), it calls:

```rust
let frame: Option<&PyAny> = get_frame.call1(py, (generator,))?.extract()?;
if let Some(f) = frame {
    let line: u32 = f.getattr("f_lineno")?.extract()?;
    // f.getattr("f_code")?.getattr("co_filename") etc. also available
}
```

The VM never reads `gi_frame` directly from the generator. The callback encapsulates all generator-structure knowledge. This replaces the current `generator_current_line()` function entirely.

The frame object is opaque to the VM — it reads standard Python frame attributes (`f_lineno`, `f_code`). The VM doesn't need to know whether the frame came from a bridge generator, a user generator, or something else entirely.

### 3.5 Frame Shape

`Frame::PythonGenerator` gains a `get_frame` callback:

| Field | Type | Purpose |
|-------|------|---------|
| `generator` | `PyShared` | The generator to step (`send()` / `__next__()` / `throw()`) |
| `started` | `bool` | Whether first step has occurred |
| `metadata` | `Option<CallMetadata>` | Function name, source file, initial line |
| `get_frame` | `PyShared` | User-provided callback: `Callable[[generator], Optional[FrameType]]` |

`PendingPython::StepUserGenerator` must also carry the callback:

| Field | Type | Purpose |
|-------|------|---------|
| `generator` | `PyShared` | The generator (being re-pushed after yielding) |
| `metadata` | `Option<CallMetadata>` | Carried from the frame that was popped |
| `get_frame` | `PyShared` | Carried from the frame that was popped |

**Construction**: When the VM creates `Frame::PythonGenerator` from a `DoeffGenerator`, it extracts `generator`, metadata fields, and `get_frame`. When re-pushing after a yield (via `StepUserGenerator`), the callback is carried from the previously popped frame.

### 3.6 All Generator Frame Push Sites

Every place the VM pushes `Frame::PythonGenerator` onto the frame stack must receive a `DoeffGenerator`:

| Site | Generator source | Metadata source |
|------|-----------------|-----------------|
| Entry program (`start_with_generator`) | `to_generator()` result | DoeffGenerator fields |
| Call result — program (`StartProgramFrame`) | `to_generator()` result | DoeffGenerator fields + `DoCtrl::Call` call-site fields |
| Call result — func (`CallFuncReturn`) | Function return value | DoeffGenerator fields + `DoCtrl::Call` call-site fields |
| Generator re-push (`StepUserGenerator`) | Re-push after yield | Carried from prior frame |
| Handler generator (`CallPythonHandler`) | WithHandler wrapped call result | DoeffGenerator fields |

See [IMPL-VM-PROTOCOL.md §2](IMPL-VM-PROTOCOL.md#2-generator-frame-push-sites-current-state) for current-state audit of these sites.

### 3.7 Invariants

- The VM never constructs `DoeffGenerator`. Python constructs it, VM consumes it.
- **The VM never reads `gi_frame` directly from generators.** All live location info goes through the user-provided `get_frame` callback. The VM reads standard frame attributes (`f_lineno`, `f_code`) from the returned frame, but never navigates generator internals to find it.
- The VM never walks `__doeff_inner__` chains. There is no `inner_generator`.
- The VM never imports `doeff.do`. All metadata arrives on `DoeffGenerator` fields.
- **No silent fallbacks.** If the VM expects `DoeffGenerator` and receives a raw generator, it raises an error with a clear diagnostic message identifying the object in question. No `"<anonymous>"`, no `"<unknown>"`, no `CallMetadata::anonymous()` in runtime paths.
- **Callback return semantics.** `get_frame(generator)` returns `None` **only** when the generator is exhausted (`gi_frame is None`). This is an expected terminal state, not a fallback — the VM uses `metadata.source_line` (the initial line) for trace entries in this case. If the callback returns `None` for a **live** (non-exhausted) generator, the VM raises a diagnostic error — this indicates a bug in the callback implementation, not a graceful degradation.
- **Callback exception handling.** If the callback itself raises an exception, the VM raises a diagnostic error wrapping the original. Location probing is observability, but a broken callback indicates a broken contract — it must not be silently swallowed.

### 3.8 DoeffGenerator Construction Sites

`DoeffGenerator` wraps a generator **instance** (the running coroutine with `gi_frame`, `send()`, etc.), not a generator function. The VM only ever steps instances.

Three Python-side construction sites ensure every generator entering the VM is a `DoeffGenerator`:

| Site | What it wraps | When wrapping happens |
|------|---------------|----------------------|
| **`@do` decorator** | `bridge_generator` | The `Call` DoCtrl carries `_make_gen` as its target; VM calls it; gets back `DoeffGenerator`. The `get_frame` callback navigates `bridge_gen.gi_frame.f_locals` to return the user generator's frame at probe time (see §3.3). No structural changes to the current `generator_wrapper`. |
| **`WithHandler`** | Handler generator | At handler registration time. `WithHandler` wraps the raw handler function so that when the VM calls `wrapped_handler(effect, k)`, the wrapper internally calls the original handler, wraps its generator result in `DoeffGenerator` (using the handler function's `__code__` for metadata and the default `get_frame`), and returns the `DoeffGenerator`. Transparent to handler authors. |
| **`ProgramBase.to_generator()`** | Program generator | Python-side contract. `to_generator()` implementations must return `DoeffGenerator` with an appropriate `get_frame` callback. This is enforced by the Python API, not the VM. |

Handler wrapping at `WithHandler` time ensures every handler generator is a `DoeffGenerator`:

```
WithHandler(handler_fn, expr)

  ↓ WithHandler wraps handler_fn:

  def wrapped(effect, k):
      result = handler_fn(effect, k)
      if is_generator(result):
          return DoeffGenerator(
              generator=result,
              function_name=handler_fn.__code__.co_name,
              source_file=handler_fn.__code__.co_filename,
              source_line=handler_fn.__code__.co_firstlineno,
              get_frame=_default_get_frame,
          )
      # Non-generator returns: see §3.9
      raise TypeError(
          f"Handler {handler_fn.__qualname__} must return a generator, "
          f"got {type(result).__name__}"
      )

  ↓ VM calls wrapped(effect, k) → receives DoeffGenerator
```

The VM never knows about this wrapping. It just receives `DoeffGenerator` from every `PythonCall::CallHandler` invocation.

### 3.9 Handler Non-Generator Returns

Python handlers **must** return generators. The `WithHandler` wrapper (§3.8) enforces this at the Python level: it raises `TypeError` with a diagnostic message (including the handler function name) before the VM ever sees the return value.

**Why handlers must return generators**: Handlers interact with the VM through `yield Resume(k, value)`, `yield Delegate()`, `yield Transfer(k, program)`. These are DoCtrl instructions yielded from a generator. A non-generator return has no way to issue these instructions. A handler that wants to immediately resume with a value still needs `yield Resume(k, value)` — there's no shorthand.

**Note on `DoExpr` returns**: If a handler returns a `DoExpr` (e.g., `Resume(k, value)` without `yield`), this is a user error — they forgot `yield`. The wrapper's `TypeError` catches this because a `DoExpr` is not a generator. The error message should hint at the likely cause: "did you forget `yield`?"

### 3.10 No Synthetic Generators in the VM

The VM does not create Python generators internally. All generators entering the VM are `DoeffGenerator` instances constructed by Python. There are no `PyModule::from_code` helpers, no string-literal Python code in Rust, no synthetic generator wrappers.

Handler return normalization is the responsibility of `WithHandler` (§3.8, §3.9). `DoExprBase` → generator conversion is handled by Python-side `to_generator()` methods.

See [IMPL-VM-PROTOCOL.md §3.1](IMPL-VM-PROTOCOL.md#31-wrap_expr_as_generator-and-wrap_return_value_as_generator) for the current helpers that must be eliminated.

### 3.11 Effects Do Not Carry Creation Context

Effects are pure data. They do not carry `created_at`, `EffectCreationContext`, or any per-creation stack trace metadata. Live location information is provided by the `get_frame` callback on `DoeffGenerator` (§3.4), which runs only on cold paths (trace assembly, error reporting) — never on the effect-creation hot path.

The `get_frame` callback returns a Python frame object. The trace system extracts `f_lineno`, `f_code.co_filename`, etc. from this frame — equivalent to the data previously captured per-yield but at zero hot-path cost.

See [IMPL-VM-PROTOCOL.md §3.2](IMPL-VM-PROTOCOL.md#32-effect-created_at--effectcreationcontext) for migration steps from the current `created_at` mechanism.

---

## 4. Exception Traceback Protocol

### 4.1 Problem

When the VM encounters an uncaught exception, it assembles trace data (`Vec<TraceEntry>`) and needs to deliver it to Python. The delivery mechanism must not use dunder attributes on exception objects.

### 4.2 Solution: `DoeffTracebackData` PyClass via RunResult

**`DoeffTracebackData`** is a frozen Rust PyClass that carries the trace entries as a typed object:

| Field | Type | Purpose |
|-------|------|---------|
| `entries` | `Py<PyAny>` (Python list of trace entry tuples) | The assembled active-chain trace data |

The VM delivers `DoeffTracebackData` as a field on `PyRunResult`, not as an attribute on the exception object.

### 4.3 RunResult Shape

`PyRunResult` gains a `traceback_data` field:

| Field | Type | Purpose |
|-------|------|---------|
| `result` | `Py<PyAny>` | `Ok(value)` or `Err(exception)` |
| `store` | `Py<PyAny>` | Final store state |
| `trace` | `Option<Py<PyAny>>` | Full chronological trace (when `trace=True`) |
| `traceback_data` | `Option<Py<PyAny>>` | `DoeffTracebackData` for error cases |

### 4.4 Invariants

- The VM never sets attributes on exception objects. Trace data is delivered via `RunResult.traceback_data`.
- The VM never imports `doeff.traceback`. Traceback projection (building `DoeffTraceback` from raw data) is a Python-side concern performed by the `RunResult` consumer.
- The VM never reads `__doeff_traceback__` from exceptions. Display/rendering is Python-side.

---

## 5. Call Metadata Protocol

### 5.1 CallMetadata Fields

| Field | Source | Also on DoeffGenerator? | Purpose |
|-------|--------|-------------------------|---------|
| `frame_id` | VM-internal (`fresh_frame_id()`) | No | Unique frame identifier for trace correlation |
| `function_name` | `PyCall.meta` | Yes | Overlaps — DoeffGenerator is the sole source for generator frames (see §5.2) |
| `source_file` | `PyCall.meta` | Yes | Overlaps — same rule |
| `source_line` | `PyCall.meta` | Yes | Overlaps — same rule |
| `args_repr` | `PyCall.meta` | No | Call-site specific: string repr of arguments at the call site |
| `program_call` | `PyCall.meta` | No | Call-site specific: reference to the program call object |

### 5.2 Relationship to DoeffGenerator

When a `DoCtrl::Call` produces a generator (the common case for `@do` functions), the resulting `DoeffGenerator` carries its own `function_name`, `source_file`, `source_line`. **DoeffGenerator is the sole source of truth** for these fields on generator frames — not "takes precedence over" Call metadata, but the only authority. The VM reads location metadata from the DoeffGenerator when constructing the frame's `CallMetadata`, ignoring any overlapping values on `DoCtrl::Call`.

`DoCtrl::Call` retains its `metadata` field for:
- **Non-generator calls**: when `f` returns a plain value, no `DoeffGenerator` is created. Call metadata is the only source.
- **Call-site specific fields**: `args_repr` and `program_call` describe the call site, not the callee. These have no equivalent on `DoeffGenerator` and are merged into the frame's `CallMetadata` alongside DoeffGenerator's location fields.
- **Future extensibility**: Call may carry additional call-site metadata that doesn't belong on the generator.

### 5.3 Metadata Construction Algorithm

When the VM pushes a `Frame::PythonGenerator`, it constructs `CallMetadata` as follows:

**Case 1: Generator from `DoCtrl::Call` (entry, program call, func call)**
```
frame.metadata = CallMetadata {
    frame_id:      fresh_frame_id(),
    function_name: doeff_gen.function_name,      // from DoeffGenerator (sole source)
    source_file:   doeff_gen.source_file,         // from DoeffGenerator (sole source)
    source_line:   doeff_gen.source_line,          // from DoeffGenerator (sole source)
    args_repr:     call_ctrl.metadata.args_repr,   // from DoCtrl::Call (call-site only)
    program_call:  call_ctrl.metadata.program_call, // from DoCtrl::Call (call-site only)
}
```

**Case 2: Generator from handler invocation (`CallPythonHandler`)**
```
frame.metadata = CallMetadata {
    frame_id:      fresh_frame_id(),
    function_name: doeff_gen.function_name,   // from DoeffGenerator (sole source)
    source_file:   doeff_gen.source_file,      // from DoeffGenerator (sole source)
    source_line:   doeff_gen.source_line,       // from DoeffGenerator (sole source)
    args_repr:     None,                        // no call-site metadata for handlers
    program_call:  None,                        // no call-site metadata for handlers
}
```

**Case 3: Generator re-push after yield (`StepUserGenerator`)**
```
frame.metadata = carried_from_popped_frame   // no reconstruction needed
```

### 5.4 Invariants

- `DoCtrl::Call.metadata` must always be present. `PyCall.meta` is the authoritative source. No `__code__` fallback — `PyCall.meta` is mandatory.
- `CallMetadata::anonymous()` is not used in user-facing runtime paths. It exists only for Rust-only unit tests. Any runtime path that would produce anonymous metadata must raise an error instead.

### 5.5 Map/FlatMap Callback Metadata

Map and FlatMap DoExpr variants carry metadata about the mapper/binder function, populated by the Python side at DoExpr construction time:

```
Map {
    source: PyShared,
    mapper: PyShared,
    mapper_meta: CallMetadata,  // from mapper.__code__ at construction time
}
```

When the VM synthesizes internal `DoCtrl::Call` instructions for map/flat_map callbacks, it uses the provided `mapper_meta`. Metadata extraction happens on the Python side at DoExpr construction — the VM never probes `__code__` from mapper functions.

The mapper/binder typically returns a plain value (map) or DoExpr (flat_map), not a generator. No DoeffGenerator is involved. The metadata is used only for trace entries identifying the callback in call stacks.

See [IMPL-VM-PROTOCOL.md §3.4](IMPL-VM-PROTOCOL.md#34-mapflatmap-callback-metadata) for current violations.

---

## 6. Handler `can_handle` Protocol (Handler-Level, Not VM-Level)

The VM calls `handler.can_handle(effect)` to find a matching handler. This is a handler decision, not a VM decision. The VM doesn't know or care HOW the handler decides — it just asks.

`RustProgramHandler` implementations (state, reader, writer, scheduler) use `isinstance` checks against their own PyClass types (e.g., `obj.downcast::<PyGet>()`). This is clean — it's typed, and it's handler-internal.

The `__doeff_scheduler_*` class attributes on effect PyClasses (in effect.rs) are vestigial — they exist for legacy Python-side detection but are not used by the VM core. Whether to keep or remove them is a handler-level decision, not a VM protocol concern.

---

## 7. Summary of Protocol Surface After This Spec

### VM → Python (typed outputs)

| Data | Type | Delivery |
|------|------|----------|
| Run result | `PyRunResult` PyClass | `run()` / `async_run()` return value |
| Trace data | `DoeffTracebackData` PyClass | Field on `PyRunResult` |
| DoCtrl primitives | `PyDoCtrlBase` subclasses | Yielded by Python generators |
| Continuations | `PyContinuation` PyClass | Passed to handlers |
| Ok/Err wrappers | `PyResultOk` / `PyResultErr` PyClasses | On `PyRunResult.result` |

### Python → VM (typed inputs)

| Data | Type | Delivery |
|------|------|----------|
| Program (entry) | Any `DoExpr` | Entry to `run()` / `async_run()` |
| Generator (any frame push) | `DoeffGenerator` PyClass | Result of DoExpr evaluation, handler calls, `to_generator()` |
| `get_frame` callback | `Callable[[generator], Optional[FrameType]]` | Field on `DoeffGenerator`; VM invokes on cold paths (trace assembly, error) to get live location |
| DoCtrl instructions | `PyDoCtrlBase` subclasses | Yielded from generators |
| Effects | `PyEffectBase` subclasses | Yielded via `DoCtrl::Perform` |
| Handlers | `PyRustHandlerSentinel` or raw Python callable | In `WithHandler` / handler list |
| Call metadata | `PyCall.meta` (mandatory, typed) | On `DoCtrl::Call` |

### Eliminated Mechanisms

See [IMPL-VM-PROTOCOL.md §4](IMPL-VM-PROTOCOL.md#4-eliminated-boundary-mechanisms-summary) for the full inventory of mechanisms eliminated by this spec and their replacements.

---

## 8. Enforcement: Semgrep Rules

### Rule: No dunder attributes in VM core

```yaml
- id: vm-no-dunder-attrs
  pattern: |
    $OBJ.setattr("__doeff_$NAME__", $$$)
  message: "VM must not set __doeff_* attributes. Use typed PyClass fields."
  languages: [rust]
  severity: ERROR
  paths:
    include:
      - packages/doeff-vm/src/vm.rs
      - packages/doeff-vm/src/pyvm.rs

- id: vm-no-dunder-reads
  pattern: |
    $OBJ.getattr("__doeff_$NAME__")
  message: "VM must not read __doeff_* attributes. Use typed PyClass fields."
  languages: [rust]
  severity: ERROR
  paths:
    include:
      - packages/doeff-vm/src/vm.rs
      - packages/doeff-vm/src/pyvm.rs

- id: vm-no-hasattr-dunder
  pattern: |
    $OBJ.hasattr("__doeff_$NAME__")
  message: "VM must not probe __doeff_* attributes. Use isinstance on typed PyClass."
  languages: [rust]
  severity: ERROR
  paths:
    include:
      - packages/doeff-vm/src/vm.rs
      - packages/doeff-vm/src/pyvm.rs
```

### Rule: No Python module imports from VM core

```yaml
- id: vm-no-python-imports
  pattern: |
    $PY.import("doeff.$MODULE")
  message: "VM core must not import doeff.* modules. Use typed PyClass or NeedsPython callback."
  languages: [rust]
  severity: ERROR
  paths:
    include:
      - packages/doeff-vm/src/vm.rs
      - packages/doeff-vm/src/pyvm.rs
```

---

## 9. Resolved Questions

### Q1: Metadata redundancy between `DoCtrl::Call` and `DoeffGenerator` — RESOLVED

`DoCtrl::Call` keeps its `metadata` field. It carries call-site-specific data (`args_repr`, `program_call`) that `DoeffGenerator` does not. The overlapping fields (`function_name`, `source_file`, `source_line`) are redundant for generator calls — `DoeffGenerator` is the sole source of truth for these on generator frames. `Call` metadata remains the sole source for non-generator calls. See §5.2.

### Q2: Handler-level dunder audit — RESOLVED

None of the handler/decorator-level `__doeff_*` dunders are read by the VM. They are out of scope for this spec. A separate handler-level cleanup can address them. See [IMPL-VM-PROTOCOL.md §5](IMPL-VM-PROTOCOL.md#5-handler-level-dunder-audit-out-of-scope) for the full audit table.

### Q3: VM fallback paths — RESOLVED

All silent fallbacks in VM runtime paths are prohibited. See C7. `PyCall.meta` is mandatory. `CallMetadata::anonymous()` is not used in user-facing runtime paths.

### Q4: VM entry interface — RESOLVED

`run()` / `async_run()` accepts DoExpr, not DoeffGenerator. See C8. DoeffGenerator is the wrapper for generators produced during DoExpr evaluation, not an entry-point type.

### Q5: Handler generators need DoeffGenerator — RESOLVED

Handler generators must be wrapped in DoeffGenerator because they appear in the active-chain traceback. Wrapping happens at `WithHandler` registration time — the handler function is wrapped so its generator result is automatically a DoeffGenerator. See §3.8.

---

## 10. Resolved Design Questions

### D1: Handler generator wrapping mechanism — RESOLVED

`WithHandler` wraps handler functions at registration time. When the VM calls the wrapped handler with `(effect, k)`, it receives `DoeffGenerator` directly. The VM never sees raw handler generators. See §3.8.

### D2: `to_generator_strict` accepted types — RESOLVED

`to_generator()` is a Python-side method. It is the responsibility of `ProgramBase` implementations to return `DoeffGenerator`. The VM's `to_generator_strict` checks that the result is `DoeffGenerator`; if not, it raises an error. See §3.7 (no silent fallbacks).

### D3: Continuation frame snapshots with `get_frame` — RESOLVED

Continuations snapshot frames as `Arc<Vec<Frame>>`. These snapshots are in-memory only and live within a single VM execution — no cross-process or cross-version persistence. The `get_frame` callback is a `PyShared` (reference-counted Python object), safe to clone into continuation snapshots — the callback object itself is immutable.

### D4: `get_frame` on exhausted generators — RESOLVED

When a generator is exhausted (returned or threw), its `gi_frame` becomes `None`. The `get_frame` callback returns `None` in this case — this is the only valid reason for a `None` return (see §3.7).

This is irrelevant to the protocol because:
1. Exhausted generators have already been **popped** from the frame stack (their `GenReturn` or `GenError` outcome removes them).
2. `get_frame` is only called for **live frames** during trace assembly (`supplement_with_live_state`).
3. A generator that is both exhausted AND on the frame stack is a VM bug. If `get_frame` returns `None` for a frame that should be live, the VM raises a diagnostic error per §3.7.
