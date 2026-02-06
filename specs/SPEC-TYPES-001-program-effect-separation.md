# SPEC-TYPES-001: Program / Effect Separation — Draft Spec

## Status: WIP Discussion Draft (Rev 3)

## Context

The current doeff Python framework has `EffectBase(ProgramBase)` — effects inherit
from programs. This was done so users can write `some_kleisli(Ask("hello"), Get("key"))`
and have effects auto-unwrap. But this conflates two distinct concepts through
inheritance, causing:

1. `classify_yielded` ordering hacks (effects must be caught before programs)
2. Every effect has `to_generator()` — structurally indistinguishable from programs
3. The Rust VM needs special-case logic for what should be a clean type distinction
4. Type-level reasoning breaks (an Effect is not "runnable")

---

## 1. Design Principles

### 1.1 Two VM primitives

The Rust VM operates on two fundamental concepts:

- **`Call(program, metadata)`** — a control primitive (like `WithHandler`, `Resume`).
  "Run this program's generator and record where it came from." The VM handles it
  directly: calls `to_generator()`, pushes the generator frame with `CallMetadata`.
  No dispatch, no handler stack involvement. This is the doeff equivalent of a
  function call in Koka/OCaml.

  The metadata carries the caller's identity (function_name, source_file, source_line)
  and optionally a reference to the `KleisliProgramCall` for rich introspection.
  Metadata is extracted by the **driver** (with GIL) during `classify_yielded`, then
  passed to the VM as part of the `Call` primitive. The VM stores it on the
  `PythonGenerator` frame — no GIL needed after classification.

  **Backward compat**: `Yielded::Program` (the existing path with no metadata) is
  kept for non-KPC programs. The VM handles it identically but with `metadata: None`.

- **Effects** — dispatched through the handler stack via `start_dispatch`.
  Handlers intercept, handle, delegate, or forward them.

### 1.2 Call is syntax, KleisliProgramCall is an effect

These are at different levels:

| Concept | Type | Who handles | Example |
|---------|------|-------------|---------|
| Run a program | `Call(program, metadata)` (control primitive) | VM directly | `yield some_program` |
| Resolve args + call @do func | `KleisliProgramCall` (effect) | KPC handler | `my_do_func(x, y)` |

The KPC handler uses `Call` internally to run sub-programs during arg resolution.
The VM never needs to know about `@do`.

### 1.3 Why KPC is an effect, not syntax

Arg resolution scheduling is a **handler concern**:
- Sequential resolution? Concurrent (Gather)? Cached? Retried? Mocked?
- The handler decides. Different strategies for different contexts.
- Users who want custom resolution install a different KPC handler.

### 1.4 Program vs Effect — no inheritance

**Program**: A computation thunk. Has `to_generator()`. Immutable. Can be run
multiple times. Can be composed (`map`, `flat_map`, `>>`, `+`). Can be passed
as a value. `Program.pure(1)`, `fetch_user(42)`, `x.map(f)` are all Programs.

**Effect**: A request. Pure data. Frozen. No `to_generator()`. No lifecycle.
Yielded by programs, dispatched through the handler stack, resolved by handlers.

These are SEPARATE types with NO inheritance relationship.

---

## 2. Type Hierarchy

### Current (wrong)

```
ProgramBase                    ← has to_generator()
    │
EffectBase(ProgramBase)        ← ALSO has to_generator() (inherits)
 /    |    \    \
Get  Put  Ask  SpawnEffect ... ← every effect IS-A program
```

### Proposed (correct)

```
ProgramBase                    EffectBase
(computation thunk)            (request — pure data)
     │                              │
KleisliProgramCall             Get, Put, Modify, Ask, Tell,
PureProgram                    SpawnEffect, GatherEffect, ...
DerivedProgram                 KleisliProgramCall (!)
                               user-defined effects
```

Note: `KleisliProgramCall` appears in BOTH columns because:
- It IS an `EffectBase` (a request: "resolve my args and call my function")
- It also carries metadata from `ProgramBase`-like features (function_name, etc.)
- But it does NOT have `to_generator()` — it is NOT a Program

When the KPC handler resolves a KPC, it produces a `Call(program)` to run
the kernel generator. The Call is a control primitive the VM handles directly.

---

## 3. The KPC Handler

### 3.1 Architecture

```
User code yields KleisliProgramCall(f, [Ask("key"), fetch_user(42)], {})
         │
         ▼ dispatched as effect
   ┌─────────────────────────────────────────────┐
   │  KPC Handler (RustHandlerProgram)           │
   │                                              │
   │  1. Read auto_unwrap_strategy from KPC      │
   │  2. Classify args: unwrap vs pass-as-is     │
   │  3. Resolve unwrap-marked args:             │
   │     - Program arg → yield Call(prog, meta)  │
   │     - Effect arg  → yield effect (dispatch) │
   │     - Plain value → use as-is               │
   │  4. Call f.kernel(*resolved_args)            │
   │  5. yield Call(result_program, meta)         │
   │  6. Resume(k, result)                        │
   └─────────────────────────────────────────────┘
```

### 3.2 Annotation-aware auto-unwrap

The KPC handler MUST respect type annotations to decide which args to unwrap.
This is critical for enabling `@do` functions that transform programs:

```python
@do
def run_both(a: int, b: int) -> Program[tuple]:
    return (a, b)
# a and b are auto-unwrapped — plain type annotations

@do
def transform_program(p: Program[int]) -> Program[int]:
    val = yield p  # user manually yields the program
    return val * 2
# p is NOT unwrapped — annotated as Program[T]

@do
def inspect_effect(e: EffectBase) -> Program[str]:
    return type(e).__name__
# e is NOT unwrapped — annotated as EffectBase
```

### 3.3 Classification rules

The `_AutoUnwrapStrategy` is computed at decoration time from type annotations
and stored on the `KleisliProgramCall`. The KPC handler reads it to decide
per-arg behavior.

**DO unwrap** (`should_unwrap = True`) when annotation is:
- Plain types: `int`, `str`, `dict`, `User`, etc.
- No annotation (default: unwrap)
- Any type that is NOT a Program/Effect family type

**DO NOT unwrap** (`should_unwrap = False`) when annotation is:
- `Program`, `Program[T]`, `ProgramBase`, `ProgramBase[T]`
- `ProgramLike`, `ProgramLike[T]`
- `Effect`, `Effect[T]`, `EffectBase`
- Any subclass of `EffectBase` (e.g., custom effect types)
- Any subclass of `ProgramBase` (excluding EffectBase subclasses — but after
  separation this exclusion is unnecessary since EffectBase won't subclass ProgramBase)
- `Optional[Program[T]]`, `Program[T] | None`, `Annotated[Program[T], ...]`

**String annotation handling** (for `from __future__ import annotations`):
- Supports quoted strings, `Optional[...]`, `Annotated[...]`, union `|`
- Matches normalized strings: `"Program"`, `"Program[...]"`, `"ProgramBase"`,
  `"ProgramBase[...]"`, `"ProgramLike"`, `"ProgramLike[...]"`,
  `"Effect"`, `"EffectBase"`, `"Effect[...]"`, etc.

**Parameter kinds**:
- `POSITIONAL_ONLY`: indexed in `strategy.positional`
- `POSITIONAL_OR_KEYWORD`: indexed in both `strategy.positional` and `strategy.keyword`
- `KEYWORD_ONLY`: in `strategy.keyword`
- `VAR_POSITIONAL` (`*args`): single `strategy.var_positional` bool for all
- `VAR_KEYWORD` (`**kwargs`): single `strategy.var_keyword` bool for all

### 3.4 Arg resolution behavior

| Arg value | `should_unwrap` | Handler action |
|-----------|----------------|----------------|
| `ProgramBase` instance | `True` | `yield Call(program, metadata)` → use resolved value |
| `ProgramBase` instance | `False` | Pass the Program object as-is |
| `EffectBase` instance | `True` | `yield effect` → use resolved value |
| `EffectBase` instance | `False` | Pass the Effect object as-is |
| Plain value (`int`, `str`, etc.) | either | Pass through unchanged |

### 3.5 Resolution strategies

The default KPC handler is a `RustHandlerProgram` that resolves args
**sequentially** using `Call` (control primitive) for Programs and direct
effect yield for Effects:

```
// Rust handler pseudo-code:
fn start(effect: KPC, k: Continuation) -> RustProgramStep:
    strategy = effect.auto_unwrap_strategy
    for (idx, arg) in effect.args:
        if strategy.should_unwrap(idx) and is_program(arg):
            metadata = extract_call_metadata(arg)
            yield Call(arg, metadata)  // control primitive → VM runs it
            // resume(resolved_value) called by VM when program completes
        elif strategy.should_unwrap(idx) and is_effect(arg):
            yield effect(arg)          // new dispatch via start_dispatch
            // resume(resolved_value) called when handler responds
        else:
            // plain value or pass-as-is, no yield needed
    
    // All args resolved → call kernel
    metadata = extract_call_metadata(effect)  // KPC metadata
    yield Call(kernel(*resolved_args), metadata)
    // resume(result) → 
    yield Resume(k, result)
```

**IMPORTANT**: Effect args are resolved by yielding them directly as
`Yielded::Effect(effect)`, NOT via `Delegate`. `Delegate` advances within
the SAME dispatch context and requires the handler to return immediately
after (no Resume). Yielding an effect directly triggers `start_dispatch`,
which creates a NEW dispatch context. The KPC handler resumes with the
resolved value when the new dispatch completes.

For **concurrent resolution**, a user installs a different handler that uses
`GetHandlers` + `CreateContinuation` + `ResumeContinuation` to run args
in parallel via the scheduler (see Section 4).

### 3.6 The busy boundary and concurrent resolution

When the KPC handler yields effects (like `Gather`), the busy boundary
(SPEC-008 INV-8) excludes the KPC handler from `visible_handlers`.
This means programs resolved via Gather won't have the KPC handler in
their handler stack.

**Sequential resolution avoids this entirely**: `Call(program, metadata)` is a
control primitive, not dispatched — it pushes a `PythonGenerator` frame directly
onto the current segment. No busy boundary applies. Nested `@do` calls within
resolved programs work because they yield KPC effects, which dispatch from the
sub-program's scope (which includes the KPC handler in its scope_chain).

**Concurrent resolution requires the 3-yield dance**:
```
handlers = yield GetHandlers()                    // full chain from callsite
cont = yield CreateContinuation(Gather(*programs), handlers)
resolved = yield ResumeContinuation(cont, None)   // call-resume semantics
```

This is cheap for Rust handlers (3 extra `Continue` steps, ~nanoseconds).
The GIL crossing only happens for `StartProgram` at the end.

---

## 4. @do Decorator — Features to Preserve

The proposed separation MUST preserve all existing `@do` behaviors.

### 4.1 Basic contract

```python
@do
def my_func(a: int, b: str) -> Program[Result]:
    # a and b are ALWAYS resolved values (int, str)
    # NEVER Effects or Programs (unless annotated as such)
    return a + len(b)
```

The `@do` decorator:
1. Returns a `KleisliProgram[P, T]` (via `DoYieldFunction` subclass)
2. Calling it creates a `KleisliProgramCall` — does NOT execute the body
3. `KleisliProgramCall` is an `EffectBase` — dispatched to the KPC handler
4. The KPC handler resolves args, calls the kernel, returns result via `Resume`
5. Native `try/except` blocks work inside `@do` functions for effect errors

### 4.2 Non-generator early return

`@do` handles functions that don't yield (plain return):

```python
@do
def pure_func(a: int, b: int) -> Program[int]:
    return a + b  # no yields — still valid
```

The `DoYieldFunction` wrapper detects `inspect.isgenerator(gen_or_value)` is
`False` and returns immediately without entering the yield loop.

### 4.3 Metadata preservation

`@do` preserves the original function's identity for tooling and introspection:

- `__doc__`, `__name__`, `__qualname__`, `__module__`, `__annotations__`
- `__signature__` (via `inspect.signature`)
- `original_func` / `original_generator` property on `DoYieldFunction`

### 4.4 Method decoration

`KleisliProgram` implements `__get__` (descriptor protocol), so `@do` works
on class methods:

```python
class Service:
    @do
    def fetch(self, id: int) -> Program[dict]:
        data = yield Ask(f"item:{id}")
        return data
```

### 4.5 Kleisli composition

`KleisliProgram` provides composition operators that must be preserved:

```python
# and_then_k / >> — Kleisli composition
pipeline = fetch_user >> enrich_profile >> validate

# fmap — functor map over result
uppercased = fetch_name.fmap(str.upper)

# partial — partial application
fetch_by_id = fetch_item.partial(category="books")
```

### 4.6 KleisliProgramCall metadata

`KleisliProgramCall` carries debugging/observability data:

| Field | Purpose |
|-------|---------|
| `kleisli_source` | Reference to originating `KleisliProgram` |
| `function_name` | Human-readable name for tracing |
| `created_at` | `EffectCreationContext` for call tree reconstruction |
| `auto_unwrap_strategy` | Annotation-derived arg classification |
| `execution_kernel` | The actual generator function to call |

### 4.7 Derived calls (`create_derived`)

`KleisliProgramCall.create_derived()` creates transformed calls that preserve
metadata from the parent. Used by `map()` and `flat_map()`:

```python
mapped = my_program().map(lambda x: x + 1)
# → KleisliProgramCall with parent's metadata but new execution_kernel
```

---

## 5. Call Stack Tracking

### 5.1 The problem: current Rust VM has no call stack tracking

The current Rust VM's `Frame::PythonGenerator` has exactly two fields:
`generator: Py<PyAny>` and `started: bool`. **No metadata of any kind.**

When `Yielded::Program` is processed, the program object is consumed by
`to_generator()` and the resulting generator is stored. The program's
metadata (function_name, source_file, source_line, kleisli_source, created_at)
is discarded — making call stack reconstruction impossible.

### 5.2 Current mechanism (Python CESK — what we must preserve)

The Python CESK stores rich metadata on `ReturnFrame.program_call`:

```python
# cesk/frames.py
@dataclass
class ReturnFrame:
    generator: Generator
    saved_env: Environment
    program_call: KleisliProgramCall | None = None     # ← THE METADATA
    kleisli_function_name: str | None = None
    kleisli_filename: str | None = None
    kleisli_lineno: int | None = None
```

The call stack is reconstructed on demand by walking K:

```python
# core_handler.py — ProgramCallStackEffect handler
for frame in ctx.delimited_k:
    if isinstance(frame, ReturnFrame) and frame.program_call is not None:
        call_frame = CallFrame(
            kleisli=frame.program_call.kleisli_source,
            function_name=frame.program_call.function_name,
            args=frame.program_call.args,
            ...
        )
```

### 5.3 Rust VM mechanism — `Call` carries `CallMetadata`

This is why `Call` must be a `ControlPrimitive` (not just `Yielded::Program`).
The `Call` primitive carries metadata alongside the program:

```rust
/// Metadata about a program call for call stack reconstruction.
/// Stored on PythonGenerator frames. Extracted by driver (with GIL)
/// before being passed to the VM.
#[derive(Debug, Clone)]
pub struct CallMetadata {
    /// Human-readable function name (e.g., "fetch_user")
    pub function_name: String,
    /// Source file where the @do function is defined
    pub source_file: String,
    /// Line number in source file
    pub source_line: u32,
    /// Optional: reference to the full KleisliProgramCall for rich introspection
    /// (e.g., args, kwargs, kleisli_source). Py<PyAny> requires GIL to access.
    pub program_call: Option<Py<PyAny>>,
}
```

The updated `Frame::PythonGenerator`:

```rust
Frame::PythonGenerator {
    generator: Py<PyAny>,
    started: bool,
    metadata: Option<CallMetadata>,  // NEW — populated by Call primitive
}
```

### 5.4 Metadata extraction flow

```
User code yields KleisliProgramCall or ProgramBase
    │
    ▼ driver classify_yielded (GIL held)
    │
    ├─ KPC detected → extract metadata WITH GIL:
    │   function_name = kpc.function_name
    │   source_file   = kpc.kleisli_source.original_func.__code__.co_filename
    │   source_line   = kpc.kleisli_source.original_func.__code__.co_firstlineno
    │   program_call  = Some(kpc_ref)
    │   → emit Yielded::Primitive(ControlPrimitive::Call { program, metadata })
    │
    ├─ Non-KPC ProgramBase → no metadata available
    │   → emit Yielded::Program(obj)  (backward compat, metadata: None)
    │
    ▼ VM handles Call:
    1. Emit StartProgram to driver → get generator
    2. Push Frame::PythonGenerator { generator, started: false, metadata: Some(m) }
    
    ▼ VM handles Yielded::Program (legacy):
    1. Emit StartProgram to driver → get generator
    2. Push Frame::PythonGenerator { generator, started: false, metadata: None }
```

**Key design point**: Metadata extraction happens in the driver (with GIL),
not in the VM. This is consistent with SPEC-008's architecture — the driver
does all Python interaction, the VM stays GIL-free.

### 5.5 `GetCallStack` control primitive

`GetCallStack` is a `ControlPrimitive` (like `GetHandlers`) that walks
segments and frames, collecting `CallMetadata` from each `PythonGenerator`
frame that has it:

```rust
ControlPrimitive::GetCallStack => {
    let mut stack = Vec::new();
    // Walk current segment + caller chain
    let mut seg_id = self.current_segment;
    while let Some(id) = seg_id {
        let seg = &self.segments[id.index()];
        for frame in seg.frames.iter().rev() {
            if let Frame::PythonGenerator { metadata: Some(m), .. } = frame {
                stack.push(m.clone());
            }
        }
        seg_id = seg.caller;
    }
    self.mode = Mode::Deliver(Value::CallStack(stack));
    StepEvent::Continue
}
```

No GIL needed. No Python interaction. Pure Rust frame walk. For richer
introspection (args, kwargs), user code can access `metadata.program_call`
via a Python-side effect that reads the `Py<PyAny>` reference with GIL.

### 5.6 How the KPC handler populates metadata

When the KPC handler yields `Call(kernel_program, metadata)` for arg
resolution or kernel execution, the metadata comes from the `KleisliProgramCall`
effect it received. The handler extracts it once at `start()` time and
attaches it to each `Call` it emits:

```rust
// KPC handler (RustHandlerProgram) pseudo-code:
fn start(effect: KPC, k_user: Continuation) -> RustProgramStep {
    let metadata = CallMetadata {
        function_name: effect.function_name.clone(),
        source_file: effect.source_file.clone(),
        source_line: effect.source_line,
        program_call: Some(effect.as_py_ref()),
    };
    
    // ... resolve args ...
    
    // Call kernel with metadata
    RustProgramStep::Yield(Yielded::Primitive(ControlPrimitive::Call {
        program: kernel,
        metadata,
    }))
}
```

---

## 6. Effect Taxonomy (Revised)

```
Category             Examples                        Handled by
────────────────────────────────────────────────────────────────────
Control primitives   Call(program, metadata),         VM directly
                     WithHandler, Resume,
                     Transfer, Delegate,
                     GetContinuation, GetHandlers,
                     GetCallStack,
                     CreateContinuation,
                     ResumeContinuation,
                     PythonAsyncSyntaxEscape

Standard effects     Get, Put, Modify, Ask, Tell     state/reader/writer handler
Kleisli resolution   KleisliProgramCall              KPC handler
Scheduling           Spawn, Gather, Race             scheduler handler
Custom               user-defined                    user handler
```

`KleisliProgramCall` is a regular effect — it goes through the handler stack
like any other effect. The KPC handler is a user-space handler (default
provided as `RustHandlerProgram`), not a VM-internal component.

---

## 7. Impact on classify_yielded (Rust VM)

With Program/Effect separation, `classify_yielded` becomes clean:

```
Phase 1: isinstance for Rust pyclass types
    ├── Control primitives: PyWithHandler, PyResume, PyTransfer,
    │   PyDelegate, ...
    ├── Standard effects: PyGet, PyPut, PyModify, PyAsk, PyTell
    ├── Scheduler effects: PySpawn, PyGather, PyRace, ...
    └── Kleisli: PyKleisliProgramCall → Yielded::Effect

Phase 2: string-based match (Python classes, backward compat)
    ├── Standard effects: "StateGetEffect", "AskEffect", ...
    ├── User-space scheduler effects: "SpawnEffect", "GatherEffect", ...
    └── Internal scheduler effects: "_Scheduler*"

Phase 3: has to_generator()? → UPGRADE TO Call
    ├── If KPC-like (has function_name, kleisli_source):
    │       Extract CallMetadata (function_name, source_file, source_line)
    │       → Yielded::Primitive(ControlPrimitive::Call { program, metadata })
    └── Otherwise (plain ProgramBase, no metadata):
            → Yielded::Program(obj)  (legacy path, metadata: None)
    NO AMBIGUITY — Effects don't have to_generator() anymore

Phase 4: Reject primitives (int, str, ...) → Unknown

Phase 5: Custom effect fallback → Effect::Python(obj)
```

The critical improvements:
- **Phase 3 is unambiguous**. If something has `to_generator()`, it IS a program.
  Effects never have it. No ordering hacks.
- **KPCs detected in Phase 1 go to Effect dispatch** (not Program path). The KPC
  handler resolves args and emits `Call` primitives for sub-programs.
- **Phase 3 extracts metadata** from recognized program objects before emitting
  `Call`. Programs yielded directly by user code (not via KPC handler) get
  metadata extracted here by the driver.

---

## 8. Migration Path

### Phase A: Spec + Rust types
1. Finalize this spec (SPEC-TYPES-001) and update SPEC-008
2. Add `CallMetadata` struct in Rust VM
3. Add `metadata: Option<CallMetadata>` to `Frame::PythonGenerator`
4. Add `Call { program, metadata }` as a `ControlPrimitive` variant
5. Add `GetCallStack` as a `ControlPrimitive` variant
6. Implement metadata extraction in driver's `classify_yielded` (KPC → Call upgrade)
7. Keep existing `Yielded::Program` handling as fallback (metadata: None)

### Phase B: KPC as effect
1. Make `KleisliProgramCall` inherit from `EffectBase` (not `ProgramBase`)
2. Remove `to_generator()` from `KleisliProgramCall`
3. Implement default KPC handler as `RustHandlerProgram`
4. Update `classify_yielded` to classify KPC as `Yielded::Effect`
5. Update presets to include KPC handler
6. Update `@do` decorator — `KleisliProgram.__call__` yields KPC effect

### Phase C: Program/Effect separation
1. Remove `EffectBase(ProgramBase)` inheritance
2. `EffectBase` becomes standalone (no `to_generator()`)
3. Remove `classify_yielded` ordering hacks (effects-before-programs)
4. Verify all tests pass

### Phase D: Cleanup
1. Remove Python CESK v1 and v3 (avoid confusion with Rust VM semantics)
2. Remove string-based fallback from `classify_yielded` (after full migration)
3. Remove legacy Python dataclass effect implementations (if Rust path confirmed)

---

## 9. Resolved Questions

1. **`Call(program, metadata)` is a control primitive, not an effect.** Like
   function calls in Koka/OCaml. The VM handles it directly (push generator
   frame with `CallMetadata`). No dispatch. The metadata carries function_name,
   source_file, source_line — extracted by the driver with GIL.

2. **KPC is an effect, not a control primitive.** Arg resolution scheduling is a
   handler concern. Sequential, concurrent, cached, retried — the handler decides.

3. **Auto-unwrap strategy is carried on the KPC effect.** Computed at `@do`
   decoration time from type annotations, stored on `KleisliProgramCall`.
   The KPC handler reads it to classify args.

4. **Default KPC handler resolves sequentially** using `Call` for Program args
   and direct effect yield for Effect args. No busy boundary issues since `Call`
   is a control primitive (not dispatched).

5. **Effect args use direct yield, NOT Delegate.** `Delegate` advances within
   the same dispatch context and requires the handler to return immediately
   after — incompatible with multi-arg resolution. Yielding an effect directly
   triggers `start_dispatch` (new dispatch), and the KPC handler resumes with
   the resolved value.

6. **Concurrent resolution is opt-in** via a different KPC handler that uses
   `GetHandlers` + `CreateContinuation` + `ResumeContinuation`.

7. **Call stack is structural** (walked from segments/frames on demand), not
   tracked via push/pop. `GetCallStack` is a control primitive like `GetHandlers`.
   It returns `Vec<CallMetadata>` from `PythonGenerator` frames — pure Rust,
   no GIL needed.

8. **`Yielded::Program` is kept for backward compat.** Non-KPC programs that
   don't carry metadata go through the existing `Yielded::Program` → `StartProgram`
   path with `metadata: None`. Over time, `classify_yielded` can upgrade more
   program types to `Call` with metadata.

---

## 10. Open Questions

1. **Composition operators after separation**: `KleisliProgram.and_then_k`,
   `>>`, `fmap`, `partial`, `map`, `flat_map` all produce new
   `KleisliProgramCall` instances via `create_derived`. After separation,
   derived calls become `KleisliProgramCall` effects too. Is this correct?
   Or should `map`/`flat_map` produce plain Programs (with `to_generator()`)?

2. **run() entry point**: When the top-level argument to `run()` is a
   `KleisliProgramCall` (common case), it must go through the KPC handler.
   The KPC handler must be in the handler stack. Should `run()` auto-include
   the default KPC handler, or require it explicitly?

3. **Performance**: Every `@do` function call becomes an effect dispatch.
   For hot paths, this adds overhead vs current inline `to_generator()`.
   Should there be a fast-path in the VM for KPC (recognize + handle
   inline, bypassing full dispatch)?

---

## References

- SPEC-008: Rust VM internals (handler stacking, busy boundary, visible_handlers)
- SPEC-009: Public API (Rev 5)
- SPEC-EFF-005: Concurrency effects
- `doeff/program.py`: Current _AutoUnwrapStrategy, _build_auto_unwrap_strategy,
  _annotation_is_program, _annotation_is_effect implementations
- `doeff/do.py`: Current DoYieldFunction / @do decorator
- `packages/doeff-vm/src/vm.rs`: Current Yielded::Program handling, StartProgram
- `packages/doeff-vm/src/pyvm.rs`: Current classify_yielded implementation
- `packages/doeff-vm/src/scheduler.rs`: Current Spawn/Gather/scheduler handler
- BasisResearch/effectful: `Operation.__apply__` as interceptable call effect (prior art)
