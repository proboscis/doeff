# SPEC-TYPES-001: DoExpr Type Hierarchy — Draft Spec

## Status: WIP Discussion Draft (Rev 5)

## Context

The current doeff Python framework has `EffectBase(ProgramBase)` — effects inherit
from programs. This was done so users can write `some_kleisli(Ask("hello"), Get("key"))`
and have effects auto-unwrap. But this conflates concepts through inheritance:

1. `classify_yielded` ordering hacks (effects must be caught before programs)
2. Every effect has `to_generator()` — structurally indistinguishable from programs
3. The Rust VM needs special-case logic for what should be a clean type distinction
4. Type-level reasoning breaks (an Effect is not a "thunk")

This spec proposes a clean hierarchy: `DoExpr` → `Program` → `DoThunk`/`Effect`,
where Effects ARE Programs (composable) but are NOT DoThunks (no `to_generator()`).
See Section 1.4 for the full type hierarchy.

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

### 1.4 Type hierarchy — DoExpr, Program, DoThunk, Effect

The old design conflated concepts through inheritance (`EffectBase(ProgramBase)`).
The new design introduces a clean hierarchy with precise names:

**`DoExpr`** (base VM contract): The root of everything yieldable inside a
`@do` generator. "A do-expression." The VM knows how to handle any `DoExpr` —
either by running a thunk's generator or dispatching an effect through the
handler stack. Control primitives (WithHandler, Resume, Transfer) are also
`DoExpr` but are NOT composable programs.

**`Program[T]`** (`DoExpr` + user composability): The user-facing type for any
deferred doeff computation. Has `map`, `flat_map`, `>>`, `+`, `pure()`.
This is what users write in type hints and compose in pipelines:
`fetch_user(42).map(lambda u: u.name)`. Every `Program` is a `DoExpr`
(yieldable), but not every `DoExpr` is a `Program` (control primitives aren't).

```python
class DoExpr(Protocol):
    """Base: anything yieldable in a @do generator. VM handles it."""

class Program(DoExpr, Protocol[T]):
    """DoExpr + composable computation."""
    def map(self, f: Callable[[T], U]) -> Program[U]: ...
    def flat_map(self, f: Callable[[T], Program[U]]) -> Program[U]: ...
    @staticmethod
    def pure(value: T) -> Program[T]: ...
```

**`DoThunk[T]`** (`Program` + `to_generator()`): A program that the VM can run
directly by calling `to_generator()` to get a generator frame. Purely
mechanical. `PureProgram` and `DerivedProgram` are thunks.

**`Effect[T]`** (`Program` + handler dispatch): A request yielded by programs,
dispatched through the handler stack, resolved by handlers. Effects are also
`Program` — users can compose them: `Ask("key").map(str.upper)`.

```
DoExpr                        ← "yieldable in @do, VM handles it"
  │
  ├── Program[T]              ← DoExpr + composable (map, flat_map, +, pure)
  │   │
  │   ├── DoThunk[T]          ← Program + to_generator() (VM runs directly)
  │   │   ├── PureProgram
  │   │   └── DerivedProgram
  │   │
  │   └── Effect[T]           ← Program + handler dispatch
  │       ├── Ask, Get, Put, Tell, Modify, ...
  │       ├── Spawn, Gather, Race, ...
  │       ├── KleisliProgramCall
  │       └── user-defined effects
  │
  └── ControlPrimitive        ← DoExpr, but NOT Program (not composable)
      ├── WithHandler, Resume, Transfer, Delegate
      ├── GetHandlers, GetCallStack, GetContinuation
      ├── CreateContinuation, ResumeContinuation
      └── Call(thunk, metadata)
```

| Type | DoExpr | Program | DoThunk | Effect |
|------|--------|---------|---------|--------|
| PureProgram | yes | yes | **yes** | no |
| DerivedProgram | yes | yes | **yes** | no |
| KleisliProgramCall | yes | yes | no | **yes** |
| Ask, Get, Put, ... | yes | yes | no | **yes** |
| WithHandler, Resume, ... | yes | no | no | no |

Key insight: **Effects are Programs**. Users can compose any effect with
`.map()`, `.flat_map()`, etc. The `.map()` on an Effect may produce a DoThunk
(e.g., `Ask("key").map(f)` → `DerivedProgram`), but the return type is
always `Program[T]` — the user doesn't care about the concrete subtype.

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

### Proposed (correct) — DoExpr / Program / Thunk / Effect

```
DoExpr
  │
  ├── Program[T]  (= DoExpr + map/flat_map/+/pure)
  │   │
  │   ├── Thunk[T]  (= Program + to_generator())
  │   │   ├── PureProgram[T]
  │   │   └── DerivedProgram[T]
  │   │
  │   └── Effect[T]  (= Program + handler dispatch)
  │       ├── Ask[T], Get[T], Put, Tell, Modify, ...
  │       ├── Spawn, Gather, Race, ...
  │       ├── KleisliProgramCall[T]
  │       └── user-defined effects
  │
  └── ControlPrimitive  (= DoExpr only, NOT Program)
      ├── WithHandler, Resume, Transfer, Delegate
      ├── Call(thunk, metadata)
      └── GetHandlers, GetCallStack, ...
```

**All Effects are Programs**. All Programs are DoExprs. Not all DoExprs are
Programs (control primitives aren't composable).

The VM handles DoExprs through two paths:
- **Thunk path**: call `to_generator()`, push generator frame
- **Effect path**: dispatch through handler stack via `start_dispatch`
- **ControlPrimitive path**: VM handles directly (no dispatch, no generator)

User code examples showing the unified `Program[T]` type:

```python
@do
def fetch_user(id: int) -> Program[User]: ...

# Effect as Program — yieldable:
@do
def main():
    user = yield fetch_user(1)      # KPC effect → handler dispatch
    key = yield Ask("api_key")      # Ask effect → handler dispatch

# Effect as Program — composable:
name_prog = fetch_user(1).map(lambda u: u.name)
upper_key = Ask("api_key").map(str.upper)
both = fetch_user(1) + fetch_user(2)
```

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
def inspect_effect(e: Effect) -> Program[str]:
    return type(e).__name__
# e is NOT unwrapped — annotated as Effect
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
- `Program`, `Program[T]`
- `DoThunk`, `DoThunk[T]`
- `Effect`, `Effect[T]`
- `DoExpr`
- Any subclass of `Effect` (e.g., custom effect types)
- Any subclass of `DoThunk`
- `Optional[Program[T]]`, `Program[T] | None`, `Annotated[Program[T], ...]`

**String annotation handling** (for `from __future__ import annotations`):
- Supports quoted strings, `Optional[...]`, `Annotated[...]`, union `|`
- Matches normalized strings: `"Program"`, `"Program[...]"`, `"Thunk"`,
  `"DoThunk"`, `"DoThunk[...]"`, `"Effect"`, `"Effect[...]"`, `"DoExpr"`, etc.

**Parameter kinds**:
- `POSITIONAL_ONLY`: indexed in `strategy.positional`
- `POSITIONAL_OR_KEYWORD`: indexed in both `strategy.positional` and `strategy.keyword`
- `KEYWORD_ONLY`: in `strategy.keyword`
- `VAR_POSITIONAL` (`*args`): single `strategy.var_positional` bool for all
- `VAR_KEYWORD` (`**kwargs`): single `strategy.var_keyword` bool for all

### 3.4 Arg resolution behavior

| Arg value | `should_unwrap` | Handler action |
|-----------|----------------|----------------|
| `DoThunk` instance | `True` | `yield Call(thunk, metadata)` → use resolved value |
| `DoThunk` instance | `False` | Pass the DoThunk object as-is |
| `Effect` instance | `True` | `yield effect` → use resolved value (handler dispatch) |
| `Effect` instance | `False` | Pass the Effect object as-is |
| Plain value (`int`, `str`, etc.) | either | Pass through unchanged |

### 3.5 Resolution strategies

The default KPC handler is a `RustHandlerProgram` that resolves args
**sequentially** using `Call` (control primitive) for DoThunks and direct
effect yield for Effects:

```
// Rust handler pseudo-code:
fn start(effect: KPC, k: Continuation) -> RustProgramStep:
    strategy = effect.auto_unwrap_strategy
    for (idx, arg) in effect.args:
        if strategy.should_unwrap(idx) and is_do_thunk(arg):
            metadata = extract_call_metadata(arg)
            yield Call(arg, metadata)  // control primitive → VM runs thunk
            // resume(resolved_value) called by VM when thunk completes
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
3. `KleisliProgramCall` is an `Effect` — dispatched to the KPC handler
4. `KleisliProgramCall` is also a `Program` — users can compose it
   with `.map()`, `.flat_map()`, `+`, etc. before yielding
5. The KPC handler resolves args, calls the kernel, returns result via `Resume`
6. Native `try/except` blocks work inside `@do` functions for effect errors

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

### 4.7 Composition on Effects returns DoThunk

`.map()` and `.flat_map()` on ANY Effect (including KPC) return a
`DerivedProgram` (DoThunk). This is uniform — no special cases:

```python
mapped = my_program().map(lambda x: x + 1)
# → DerivedProgram (DoThunk) wrapping the original KPC

mapped = Ask("key").map(str.upper)
# → DerivedProgram (DoThunk) wrapping Ask("key")
```

The full composability chain:

```python
result = (
    fetch_user(42)              # KPC (Effect)
    .map(lambda u: u.name)      # DerivedProgram (DoThunk)
    .map(str.upper)             # DerivedProgram (DoThunk)
)
# result is a DoThunk — VM calls to_generator()
# generator yields the original KPC → handler dispatch → resolved
user = yield result
```

Every intermediate result is a `Program` (therefore a `DoExpr`) — always
yieldable, always composable. The `.map()` crosses from Effect to DoThunk,
but the user only sees `Program[T]`.

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
User code yields DoExpr (KPC, DoThunk, Effect, or ControlPrimitive)
    │
    ▼ driver classify_yielded (GIL held)
    │
    ├─ KPC detected → extract metadata WITH GIL:
    │   function_name = kpc.function_name
    │   source_file   = kpc.kleisli_source.original_func.__code__.co_filename
    │   source_line   = kpc.kleisli_source.original_func.__code__.co_firstlineno
    │   program_call  = Some(kpc_ref)
    │   → emit Yielded::Effect(kpc)  (dispatched to KPC handler)
    │
    ├─ Non-KPC DoThunk → no metadata available
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

## 6. DoExpr Taxonomy (Revised)

All yieldable values in `@do` generators are `DoExpr`. The VM classifies
each `DoExpr` and handles it according to its category:

```
DoExpr Category      Examples                        Handled by
────────────────────────────────────────────────────────────────────
ControlPrimitive     Call(thunk, metadata),           VM directly
(DoExpr only)        WithHandler, Resume,
                     Transfer, Delegate,
                     GetContinuation, GetHandlers,
                     GetCallStack,
                     CreateContinuation,
                     ResumeContinuation,
                     PythonAsyncSyntaxEscape

Effect (= Program)   Get, Put, Modify, Ask, Tell     state/reader/writer handler
                     KleisliProgramCall               KPC handler
                     Spawn, Gather, Race              scheduler handler
                     user-defined effects             user handler

DoThunk (= Program)  PureProgram, DerivedProgram     VM directly (to_generator())
```

`KleisliProgramCall` is a regular Effect — it goes through the handler stack
like any other effect. The KPC handler is a user-space handler (default
provided as `RustHandlerProgram`), not a VM-internal component.

All Effects and DoThunks are also `Program` — composable with `map`,
`flat_map`, `+`, etc.

---

## 7. Impact on classify_yielded (Rust VM)

With the DoExpr hierarchy, `classify_yielded` classifies each yielded
`DoExpr` into its VM handling path:

```
Phase 1: isinstance for Rust pyclass types
    ├── ControlPrimitive: PyWithHandler, PyResume, PyTransfer,
    │   PyDelegate, ...
    ├── Effects: PyGet, PyPut, PyModify, PyAsk, PyTell
    ├── Effects: PySpawn, PyGather, PyRace, ...
    └── Effect: PyKleisliProgramCall → Yielded::Effect

Phase 2: string-based match (Python classes, backward compat)
    ├── Effects: "StateGetEffect", "AskEffect", ...
    ├── Effects: "SpawnEffect", "GatherEffect", ...
    └── Internal scheduler effects: "_Scheduler*"

Phase 3: has to_generator()? → DoThunk → UPGRADE TO Call
    ├── If has metadata (function_name, kleisli_source):
    │       Extract CallMetadata (function_name, source_file, source_line)
    │       → Yielded::Primitive(ControlPrimitive::Call { thunk, metadata })
    └── Otherwise (plain DoThunk, no metadata):
            → Yielded::Program(obj)  (legacy path, metadata: None)
    NO AMBIGUITY — Effects don't have to_generator() anymore

Phase 4: Reject primitives (int, str, ...) → Unknown

Phase 5: Custom effect fallback → Effect::Python(obj)
```

The critical improvements:
- **Phase 3 is unambiguous**. If something has `to_generator()`, it IS a DoThunk.
  Effects never have it. No ordering hacks.
- **KPCs detected in Phase 1 go to Effect dispatch** (not DoThunk path). The KPC
  handler resolves args and emits `Call` primitives for sub-programs.
- **Phase 3 extracts metadata** from recognized DoThunk objects before emitting
  `Call`. DoThunks yielded directly by user code (not via KPC handler) get
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

### Phase B: Introduce DoExpr type hierarchy
1. Define `DoExpr` as base protocol (everything yieldable in `@do`)
2. Define `Program[T]` as `DoExpr` + composability (map, flat_map, +, pure)
3. Define `DoThunk[T]` as `Program` + `to_generator()` (PureProgram, DerivedProgram)
4. Define `Effect[T]` as `Program` + handler dispatch
5. Make `KleisliProgramCall` an `Effect` (yieldable to handler stack + composable)
6. Make all standard effects (Get, Put, Ask, ...) implement `Effect` (= `Program`)
7. Remove `to_generator()` from `KleisliProgramCall` (it is NOT a DoThunk)
8. Implement default KPC handler as `RustHandlerProgram`
9. Update `classify_yielded` to classify KPC as `Yielded::Effect`
10. Update presets to include KPC handler
11. Update `@do` decorator — `KleisliProgram.__call__` yields KPC effect
12. Ensure `create_derived()` returns KPC (preserving Effect + Program)

### Phase C: Complete separation (DoExpr hierarchy replaces old ProgramBase/EffectBase)
1. Remove `EffectBase(ProgramBase)` inheritance
2. `Effect` becomes `Program` subtype (composable, handler-dispatched)
3. `DoThunk` retains `to_generator()` for PureProgram, DerivedProgram
4. `ControlPrimitive` is `DoExpr` only (not `Program`, not composable)
5. Remove `classify_yielded` ordering hacks (effects-before-programs)
6. Verify all tests pass

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

9. **Type hierarchy: DoExpr / Program / DoThunk / Effect (Rev 5).**
   The old design had `EffectBase(ProgramBase)` — all effects inherited
   `to_generator()`. The new design introduces a clean hierarchy:

   - `DoExpr`: base of everything yieldable in `@do` generators
   - `Program[T]`: `DoExpr` + composability (`map`, `flat_map`, `+`, `pure`)
   - `DoThunk[T]`: `Program` + `to_generator()` (VM runs directly)
   - `Effect[T]`: `Program` + handler dispatch
   - `ControlPrimitive`: `DoExpr` only (not composable)

   **Effects ARE Programs.** Users can compose any effect:
   `Ask("key").map(str.upper)`. KPC is an Effect (therefore also a Program).
   The key difference from the old design: Effects do NOT have `to_generator()`.
   Only DoThunks do. The VM handles them through different paths (handler
   dispatch vs generator frame).

10. **Naming conventions (Rev 5).** `DoExpr` and `DoThunk` use the `Do-`
    prefix to tie them to the `@do` decorator and the doeff framework.
    `Program` and `Effect` are unprefixed for user ergonomics — these are
    the types users write in annotations and interact with daily.

11. **run() requires explicit KPC handler (Rev 5).** The KPC handler is not
    auto-installed. If a KPC is dispatched with no handler, the VM errors.
    Users provide it via presets or explicit handler list.

12. **Effect.map() returns DerivedProgram / DoThunk (Rev 5).** ALL effects
    cross from Effect to DoThunk when composed with `.map()` — uniformly,
    including KPC. No special cases. The thunk wraps the effect in a
    generator that yields it.

---

## 10. Open Questions

1. ~~**Composition operators after separation**~~

   **RESOLVED (Rev 4)**: Derived calls remain `KleisliProgramCall` (Effect)
   instances. The full composition chain produces Effects at every step —
   each one yieldable AND further composable. See Section 4.7.

2. ~~**run() entry point**~~

   **RESOLVED (Rev 5)**: `run()` does NOT auto-include the KPC handler.
   The handler stack must be provided explicitly. If a KPC is yielded and
   no KPC handler is installed, the VM raises an error. This is intentional:
   the KPC handler is a user-space handler, not a VM builtin. Users must
   configure it via presets or explicit handler installation.

   ```python
   # Correct — KPC handler provided:
   run(fetch_user(42), handlers=[kpc_handler(), state_handler()])
   # or via preset:
   run(fetch_user(42), preset=default_preset)

   # Error — no KPC handler:
   run(fetch_user(42))  # → raises: no handler for KleisliProgramCall
   ```

3. **Performance**: Every `@do` function call becomes an effect dispatch.
   For hot paths, this adds overhead vs current inline `to_generator()`.
   Should there be a fast-path in the VM for KPC (recognize + handle
   inline, bypassing full dispatch)?

4. ~~**Effect.map() return type**~~

   **RESOLVED (Rev 5)**: `Effect.map(f)` returns a `DerivedProgram` (DoThunk).
   The effect is wrapped in a generator thunk:

   ```python
   class Effect(Program[T]):
       def map(self, f: Callable[[T], U]) -> Program[U]:
           effect = self
           def thunk():
               result = yield effect   # yields self as Effect → handler dispatch
               return f(result)
           return DerivedProgram(thunk)
   ```

   This applies uniformly to ALL Effects, including `KleisliProgramCall`.
   `KPC.map(f)` also returns a `DerivedProgram` (DoThunk) — no special case.

   ```
   Ask("key").map(f)       → DerivedProgram (DoThunk)
   fetch_user(42).map(f)   → DerivedProgram (DoThunk)
   Get("k").map(f)         → DerivedProgram (DoThunk)
   ```

   The composed result takes the DoThunk path in the VM (`to_generator()` →
   push generator frame → generator yields original effect → handler dispatch).
   This adds one extra generator frame but is simple, uniform, and reuses
   existing `DerivedProgram` infrastructure.

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
