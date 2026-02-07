# SPEC-TYPES-001: DoExpr Type Hierarchy — Draft Spec

## Status: WIP Discussion Draft (Rev 8)

### Rev 8 changes — Effects are data. The VM is a dumb pipe.
- **Effects are `#[pyclass]` structs**: All Rust-handled effects (`Get`, `Put`, `Ask`,
  `Tell`, `Modify`, `Spawn`, `Gather`, `Race`, etc.) are `#[pyclass(frozen)]` types
  defined in Rust and exposed to Python. User-defined effects are plain Python classes
  subclassing `EffectBase`. See SPEC-008 R11-A.
- **`Effect` enum REMOVED**: No `Effect::Get { key }`, `Effect::Python(obj)`. Effects
  flow through the VM as opaque `Py<PyAny>`. The VM never inspects effect fields.
  Handlers downcast to the concrete `#[pyclass]` type themselves. See SPEC-008 R11-B.
- **`classify_yielded` is trivial**: One isinstance check for EffectBase →
  `Yielded::Effect(obj)`. No field extraction. No per-type arms. No string matching.
  The classifier does not touch effect data. See SPEC-008 R11-C.
- **Handler traits receive opaque effect**: `RustHandlerProgram::start()` takes
  `py: Python<'_>, effect: &Bound<'_, PyAny>`. Handler does the downcast.
  See SPEC-008 R11-D.

### Rev 7 changes (historical)
Removed `Yielded::Program`, string-based classify, backward-compat shims, hardcoded
effect switching. Superseded by Rev 8's opaque effect architecture.

## Context

The current doeff Python framework has `EffectBase(ProgramBase)` — effects inherit
from programs. This was done so users can write `some_kleisli(Ask("hello"), Get("key"))`
and have effects auto-unwrap. But this conflates concepts through inheritance:

1. `classify_yielded` ordering hacks (effects must be caught before programs)
2. Every effect has `to_generator()` — structurally indistinguishable from programs
3. The Rust VM needs special-case logic for what should be a clean type distinction
4. Type-level reasoning breaks (an Effect is not a "thunk")

This spec proposes `DoExpr[T]` as the universal base type for everything
yieldable in `@do` generators. All DoExprs are composable (`map`, `flat_map`,
`+`). `Program[T]` is a user-facing alias for `DoExpr[T]`. Subtypes:
`DoThunk` (has `to_generator()`), `Effect` (handler dispatch), `DoCtrl`
(VM control instructions). See Section 1.4 for the full type hierarchy.

---

## 1. Design Principles

### 1.1 Two VM primitives

The Rust VM operates on two fundamental concepts:

- **`Call(f, args, kwargs, metadata)`** — a control primitive (like `WithHandler`,
  `Resume`). "Call f with args/kwargs and run the result." The VM handles it
  directly: calls `f(*args, **kwargs)`, expects a DoThunk or generator, pushes
  the generator frame with `CallMetadata`. No dispatch, no handler stack
  involvement. This is the doeff equivalent of a function call in Koka/OCaml.

  Two usage patterns:
  - **DoThunk (no args)**: `Call(thunk, [], {}, metadata)` — VM calls
    `to_generator()` on the thunk, pushes frame.
  - **Kernel call (with args)**: `Call(kernel, args, kwargs, metadata)` — VM
    calls `kernel(*args, **kwargs)`, gets a generator/DoThunk result, pushes frame.

  The metadata carries the caller's identity (function_name, source_file, source_line)
  and optionally a reference to the `KleisliProgramCall` for rich introspection.
  Metadata is extracted by the **driver** (with GIL) during `classify_yielded`, then
  passed to the VM as part of the `Call` primitive. The VM stores it on the
  `PythonGenerator` frame — no GIL needed after classification.

  **`Yielded::Program` is REMOVED.** The variant MUST be deleted from the Rust
  enum. All DoThunks go through `DoCtrl::Call` with `CallMetadata`.
  `classify_yielded` extracts metadata where available, or uses
  `CallMetadata::anonymous()` otherwise. No fallback path exists.

- **Effects** — dispatched through the handler stack via `start_dispatch`.
  Handlers intercept, handle, delegate, or forward them.

### 1.2 Call is syntax, KleisliProgramCall is an effect

These are at different levels:

| Concept | Type | Who handles | Example |
|---------|------|-------------|---------|
| Run a callable | `Call(f, args, kwargs, metadata)` (control primitive) | VM directly | `yield some_thunk` |
| Resolve args + call @do func | `KleisliProgramCall` (effect) | KPC handler | `my_do_func(x, y)` |

The KPC handler uses `Eval` internally to resolve args (DoExprs) during arg resolution.
The VM never needs to know about `@do`.

### 1.3 Why KPC is an effect, not syntax

Arg resolution scheduling is a **handler concern**:
- Sequential resolution? Concurrent (Gather)? Cached? Retried? Mocked?
- The handler decides. Different strategies for different contexts.
- Users who want custom resolution install a different KPC handler.

### 1.4 Type hierarchy — DoExpr (= Program), DoThunk, Effect, DoCtrl

The old design conflated concepts through inheritance (`EffectBase(ProgramBase)`).
The new design has `DoExpr[T]` as the universal composable base.

**`DoExpr[T]`** (root type): Everything yieldable inside a `@do` generator.
"A do-expression." Every DoExpr produces a value `T` when the VM runs it,
and every DoExpr is composable (`map`, `flat_map`, `+`, `pure`). There is
no yieldable thing that isn't composable — if it returns T, you can `.map()` it.

**`Program[T]`** = **`DoExpr[T]`** (alias): User-facing name for `DoExpr[T]`.
Users write `Program[T]` in type hints. Internally it's `DoExpr[T]`.

```python
class DoExpr(Generic[T]):
    """Root: anything yieldable in @do. Always composable."""
    def map(self, f: Callable[[T], U]) -> DoExpr[U]: ...
    def flat_map(self, f: Callable[[T], DoExpr[U]]) -> DoExpr[U]: ...
    @staticmethod
    def pure(value: T) -> DoExpr[T]: ...

# User-facing alias
Program = DoExpr
```

**`DoThunk[T]`** (`DoExpr` + `to_generator()`): A DoExpr the VM can run
directly by calling `to_generator()` to get a generator frame. Purely
mechanical. `PureProgram` and `DerivedProgram` are thunks.

**`Effect[T]`** (`DoExpr` + handler dispatch): A request dispatched through
the handler stack, resolved by handlers. `Ask("key").map(str.upper)` works.

**`DoCtrl[T]`** (`DoExpr` + VM control): VM control instructions. They return
values (e.g., `WithHandler` returns the program's result), so they're
composable too: `WithHandler(h, prog).map(f)` works.

```
DoExpr[T]  (= Program[T])    ← root: yieldable + composable
  │
  ├── DoThunk[T]              ← to_generator() (VM runs directly)
  │   ├── PureProgram
  │   └── DerivedProgram
  │
  ├── Effect[T]               ← handler dispatch
  │   ├── Ask, Get, Put, Tell, Modify, ...
  │   ├── Spawn, Gather, Race, ...
  │   ├── KleisliProgramCall
  │   └── user-defined effects
  │
  └── DoCtrl[T]               ← VM control instructions
      ├── WithHandler, Resume, Transfer, Delegate
      ├── GetHandlers, GetCallStack, GetContinuation
      ├── CreateContinuation, ResumeContinuation
      ├── Call(f, args, kwargs, metadata)
      └── Eval(expr, handlers)
```

| Type | DoExpr | DoThunk | Effect | DoCtrl |
|------|--------|---------|--------|--------|
| PureProgram | yes | **yes** | no | no |
| DerivedProgram | yes | **yes** | no | no |
| KleisliProgramCall | yes | no | **yes** | no |
| Ask, Get, Put, ... | yes | no | **yes** | no |
| WithHandler, Call, ... | yes | no | no | **yes** |

`.map()` on any DoExpr returns a `DerivedProgram` (DoThunk). The user only
sees `Program[T]` (= `DoExpr[T]`) — the concrete subtype doesn't matter.

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

### Proposed (correct) — DoExpr (= Program) / DoThunk / Effect / DoCtrl

```
DoExpr[T]  (= Program[T])    ← root: yieldable + composable
  │
  ├── DoThunk[T]              ← to_generator() (VM runs directly)
  │   ├── PureProgram[T]
  │   └── DerivedProgram[T]
  │
  ├── Effect[T]               ← handler dispatch
  │   ├── Ask[T], Get[T], Put, Tell, Modify, ...
  │   ├── Spawn, Gather, Race, ...
  │   ├── KleisliProgramCall[T]
  │   └── user-defined effects
  │
  └── DoCtrl[T]               ← VM control instructions
      ├── WithHandler, Resume, Transfer, Delegate
      ├── Call(f, args, kwargs, metadata)
      ├── Eval(expr, handlers)
      └── GetHandlers, GetCallStack, ...
```

Every DoExpr is composable. `Program[T]` is a user-facing alias for `DoExpr[T]`.

The VM handles DoExprs through three paths:
- **DoThunk path**: call `to_generator()`, push generator frame
- **Effect path**: dispatch through handler stack via `start_dispatch`
- **DoCtrl path**: VM handles directly (no dispatch, no generator)

All paths produce a value T, so all support `.map()`:

```python
@do
def fetch_user(id: int) -> Program[User]: ...

# Effects — yieldable and composable:
name_prog = fetch_user(1).map(lambda u: u.name)   # KPC.map → DoThunk
upper_key = Ask("api_key").map(str.upper)          # Effect.map → DoThunk

# DoCtrl — also composable:
result = WithHandler(h, prog).map(lambda x: x + 1)  # DoCtrl.map → DoThunk
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
   │     - DoExpr arg → yield Eval(arg, handlers)│
   │     - Plain value → use as-is               │
   │  4. yield Call(kernel, resolved, {}, meta)  │
   │  5. Resume(k, result)                        │
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
| `DoThunk` instance | `True` | `yield Eval(arg, handlers)` → use resolved value |
| `DoThunk` instance | `False` | Pass the DoThunk object as-is |
| `Effect` instance | `True` | `yield Eval(arg, handlers)` → use resolved value |
| `Effect` instance | `False` | Pass the Effect object as-is |
| Plain value (`int`, `str`, etc.) | either | Pass through unchanged |

### 3.5 Resolution strategies

The default KPC handler is a `RustHandlerProgram` that resolves args using
`Eval(expr, handlers)` — a control primitive that evaluates any DoExpr
in a fresh scope with the given handler chain. The handler first captures
the callsite handlers via `GetHandlers`, then uses `Eval` for each arg.

```
// Default KPC handler (sequential resolution):
fn start(effect: KPC, k_user: Continuation) -> RustProgramStep:
    handlers = yield GetHandlers()
    strategy = effect.auto_unwrap_strategy
    
    resolved_args = []
    for (idx, arg) in effect.args:
        if strategy.should_unwrap(idx) and is_do_expr(arg):
            value = yield Eval(arg, handlers)
            resolved_args.push(value)
        else:
            resolved_args.push(arg)
    
    metadata = extract_call_metadata(effect)
    result = yield Call(effect.kernel, resolved_args, resolved_kwargs, metadata)
    yield Resume(k_user, result)
```

**`Eval` semantics**: `Eval(expr, handlers)` is a `DoCtrl` that
atomically creates an unstarted continuation with the given handler chain
and evaluates the DoExpr within it. The caller (KPC handler) is suspended;
when the evaluation completes, the VM resumes the caller with the result.
Internally equivalent to `CreateContinuation` + `ResumeContinuation` but
as a single step.

The DoExpr can be any yieldable value:
- **DoThunk** (PureProgram, DerivedProgram): VM calls `to_generator()`,
  runs the generator within the continuation's scope
- **Effect** (Get, Put, Ask, KPC, ...): VM dispatches through the
  continuation's handler stack via `start_dispatch`

`Eval` uses the explicit `handlers` to build the continuation's scope chain.
This preserves the full handler chain (including the KPC handler itself) for
nested `@do` calls within resolved args — avoiding busy boundary issues.

**Sequential vs concurrent**: The default handler resolves args one at a time
with `Eval` per arg. For **concurrent resolution**, a different KPC handler
wraps args in `Gather`:

```
// Concurrent KPC handler variant:
fn start(effect: KPC, k_user: Continuation) -> RustProgramStep:
    handlers = yield GetHandlers()
    exprs_to_resolve = [arg for (idx, arg) if should_unwrap(idx)]
    results = yield Eval(Gather(*exprs_to_resolve), handlers)
    // merge resolved values back with non-unwrapped args
    metadata = extract_call_metadata(effect)
    result = yield Call(effect.kernel, merged_args, merged_kwargs, metadata)
    yield Resume(k_user, result)
```

The handler decides the strategy. Users swap KPC handlers for different
resolution policies (sequential, concurrent, cached, retried, etc.).

### 3.6 Eval and the busy boundary

`Eval(expr, handlers)` sidesteps the busy boundary entirely. The
continuation created by `Eval` uses the explicit `handlers` parameter to
build its scope chain — NOT the current `visible_handlers`. Since
`GetHandlers` captures the full callsite chain (before the KPC dispatch
made anything busy), `Eval` preserves the complete handler stack for all
nested operations.

This means:
- Nested `@do` calls within resolved args find the KPC handler (it's in
  the explicit handlers list)
- State/reader/writer handlers are all visible
- No ordering or installation tricks needed

Both sequential (`Eval` per-arg) and concurrent (`Eval` with `Gather`)
resolution benefit from this — the handler chain is always explicit, never
affected by busy boundary computation.

Under the hood, `Eval` is equivalent to the 3-primitive sequence
`GetHandlers` + `CreateContinuation` + `ResumeContinuation`, collapsed
into a single atomic step. The VM creates an unstarted continuation with
the given handlers, starts it, and resumes the caller with the result.

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

This is why `Call` must be a `DoCtrl` (not just `Yielded::Program`).
The `Call` primitive carries the callable, args, kwargs, and metadata:

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
User code yields DoExpr (KPC, DoThunk, Effect, or DoCtrl)
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
    ├─ Non-KPC DoThunk → construct minimal metadata:
    │   CallMetadata { function_name: "<anonymous>", source_file: "<unknown>", source_line: 0, program_call: None }
    │   → emit Yielded::DoCtrl(DoCtrl::Call { f: obj, args: [], kwargs: {}, metadata })
    │   (Yielded::Program is DEPRECATED — never emitted)
    │
    ▼ VM handles Call(f, args, kwargs, metadata):
    1. Emit NeedsPython(CallFunc { f, args, kwargs }) to driver
    2. Driver calls f(*args, **kwargs), gets result (generator or DoThunk)
    3. If DoThunk: driver calls to_generator() → generator
    4. Push Frame::PythonGenerator { generator, started: false, metadata: Some(m) }
```

**Key design point**: Metadata extraction happens in the driver (with GIL),
not in the VM. This is consistent with SPEC-008's architecture — the driver
does all Python interaction, the VM stays GIL-free.

### 5.5 `GetCallStack` DoCtrl

`GetCallStack` is a `DoCtrl` (like `GetHandlers`) that walks
segments and frames, collecting `CallMetadata` from each `PythonGenerator`
frame that has it:

```rust
DoCtrl::GetCallStack => {
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

When the KPC handler yields `Call(kernel, args, kwargs, metadata)`, the
metadata comes from the `KleisliProgramCall` effect it received. The handler
extracts it once at `start()` time and attaches it to the final `Call`:

```rust
// KPC handler (RustHandlerProgram) pseudo-code:
fn start(effect: KPC, k_user: Continuation) -> RustProgramStep {
    let metadata = CallMetadata {
        function_name: effect.function_name.clone(),
        source_file: effect.source_file.clone(),
        source_line: effect.source_line,
        program_call: Some(effect.as_py_ref()),
    };
    
    // ... resolve args via Eval ...
    
    // Call kernel with resolved args and metadata
    RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Call {
        f: effect.kernel,
        args: resolved_args,
        kwargs: resolved_kwargs,
        metadata,
    }))
}
```

---

## 6. DoExpr Taxonomy (Revised)

All yieldable values in `@do` generators are `DoExpr[T]` (= `Program[T]`).
The VM classifies each DoExpr and handles it according to its subtype:

```
DoExpr Subtype       Examples                        Handled by
────────────────────────────────────────────────────────────────────
DoCtrl               Call(f, args, kwargs, metadata), VM directly
                     Eval(expr, handlers),
                     WithHandler, Resume,
                     Transfer (DoCtrl[Never]),
                     Delegate,
                     GetContinuation, GetHandlers,
                     GetCallStack,
                     CreateContinuation,
                     ResumeContinuation,
                     PythonAsyncSyntaxEscape

Effect               Get, Put, Modify, Ask, Tell     state/reader/writer handler
                     KleisliProgramCall               KPC handler
                     Spawn, Gather, Race              scheduler handler
                     user-defined effects             user handler

DoThunk              PureProgram, DerivedProgram     VM directly (to_generator())
```

All three subtypes are `DoExpr[T]`, therefore all are composable with `map`,
`flat_map`, `+`, etc. `Transfer` is `DoCtrl[Never]` — composable vacuously
(`.map(f)` type-checks but `f` never runs since Transfer aborts).

`KleisliProgramCall` is a regular Effect — it goes through the handler stack
like any other effect. The KPC handler is a user-space handler (default
provided as `RustHandlerProgram`), not a VM-internal component.

---

## 7. Impact on classify_yielded (Rust VM) [Rev 8]

With the DoExpr hierarchy, `classify_yielded` classifies each yielded
`DoExpr` into its VM handling path. **Effects are opaque data — the
classifier does not inspect them.**

```
Phase 1: isinstance for DoCtrl pyclasses (finite, VM-level)
    PyWithHandler  → Yielded::DoCtrl(WithHandler)
    PyResume       → Yielded::DoCtrl(Resume)
    PyTransfer     → Yielded::DoCtrl(Transfer)
    PyDelegate     → Yielded::DoCtrl(Delegate)
    ...other DoCtrl types...

Phase 2: isinstance(EffectBase)? → Yielded::Effect(obj)
    ONE CHECK. No field extraction. No per-type arms.
    The VM passes the object through to dispatch as-is.
    Both Rust #[pyclass] effects (Get, Put, Spawn, etc.) and
    Python user-defined effects hit this same path.

Phase 3: has to_generator()? → DoThunk → DoCtrl::Call
    Extract CallMetadata if available, else anonymous().
    Yielded::Program is deleted — DoThunks always go through Call.

Phase 4: Reject primitives (int, str, ...) → Unknown
```

That's it. Four phases. The effect classification phase is a SINGLE isinstance
check — `isinstance(obj, EffectBase)`. The classifier never reads `.key`,
`.value`, `.items`, or any effect-specific attribute. Effects are data; the
handler reads them.

### 7.1 Separation of Concerns — Effects Are Data [Rev 8]

**Principle**: The VM is a dumb pipe for effects. It does not know what `Get`
means, what `Spawn` does, or what fields `KleisliProgramCall` has. It only
knows three things: DoCtrl, Effect, DoThunk. For effects, it finds a handler
and passes the opaque object through.

**Why this works**: All Rust-handled effects (`Get`, `Put`, `Ask`, `Tell`,
`Modify`, `Spawn`, `Gather`, `Race`, etc.) are `#[pyclass(frozen)]` structs
defined in Rust (SPEC-008 R11-A). When a Rust handler receives the effect,
it downcasts to the concrete type it knows — e.g., `effect.downcast::<PyGet>()`
— and reads the Rust-native fields directly. No string parsing. No `getattr`.
The data is already in Rust.

**For Python handlers**: They receive the same object. Since `#[pyclass]` types
are proper Python objects, `isinstance(effect, Get)` works, and attribute access
(`effect.key`) works via `#[pyo3(get)]`.

**For user-defined effects**: They subclass `EffectBase` (Python) and pass
through the same `isinstance(EffectBase)` check. Python handlers handle them
with normal Python attribute access. No Rust involvement needed.

**What was deleted** (vs Rev 7):
- The `Effect` enum in Rust (`Effect::Get { key }`, `Effect::Python(obj)`, etc.)
- All field extraction in `classify_yielded` (~300 lines of `match type_str`)
- The concept of "optimized Rust variants" at the classification level
- The `effect_type` marker protocol idea (unnecessary — handlers know their types)

CODE-ATTENTION:
- `pyvm.rs`: Delete entire `match type_str { ... }` block. Replace with
  single `is_effect_base(py, obj)` → `Yielded::Effect(obj.unbind())`.
- `effect.rs`: Delete `Effect` enum. Replace with `#[pyclass]` structs.
- `vm.rs`: `Yielded::Effect(Py<PyAny>)` not `Yielded::Effect(Effect)`.
- `handler.rs`: `can_handle` and `start` receive `&Bound<'_, PyAny>`.
- All handler impls: downcast in `start()`, not pre-parsed by classifier.

---

## 8. Migration Path

### Phase A: Spec + Rust types
1. Finalize this spec (SPEC-TYPES-001) and update SPEC-008
2. Add `CallMetadata` struct in Rust VM
3. Add `metadata: Option<CallMetadata>` to `Frame::PythonGenerator`
4. Add `Call { f, args, kwargs, metadata }` as a `DoCtrl` variant
5. Add `Eval { expr, handlers }` as a `DoCtrl` variant
6. Add `GetCallStack` as a `DoCtrl` variant
7. Implement metadata extraction in driver's `classify_yielded` (KPC → Call upgrade)
8. **REMOVE `Yielded::Program`** — delete the variant from the Rust enum.
   All DoThunks go through `DoCtrl::Call` with `CallMetadata::anonymous()` when
   metadata is unavailable. No fallback path.

### Phase B: Introduce DoExpr type hierarchy
1. Define `DoExpr[T]` as composable base (map, flat_map, +, pure)
2. Define `Program[T]` as user-facing alias for `DoExpr[T]`
3. Define `DoThunk[T]` as `DoExpr` + `to_generator()` (PureProgram, DerivedProgram)
4. Define `Effect[T]` as `DoExpr` + handler dispatch
5. Define `DoCtrl[T]` as `DoExpr` + VM control (replaces ControlPrimitive)
6. Make `KleisliProgramCall` an `Effect` (handler-dispatched + composable)
7. Make all standard effects (Get, Put, Ask, ...) implement `Effect`
8. Remove `to_generator()` from `KleisliProgramCall` (it is NOT a DoThunk)
9. `.map()` on any DoExpr uniformly returns `DerivedProgram` (DoThunk)
10. Implement default KPC handler as `RustHandlerProgram`
11. Update `classify_yielded` to classify KPC as `Yielded::Effect`
12. Update presets to include KPC handler
13. Update `@do` decorator — `KleisliProgram.__call__` yields KPC effect

### Phase C: Complete separation (DoExpr hierarchy replaces old ProgramBase/EffectBase)
1. Remove `EffectBase(ProgramBase)` inheritance
2. `Effect` becomes `DoExpr` subtype (composable, handler-dispatched)
3. `DoThunk` retains `to_generator()` for PureProgram, DerivedProgram
4. `DoCtrl` replaces `ControlPrimitive` — also composable (`DoExpr[T]`)
5. `Transfer` is `DoCtrl[Never]` (composable vacuously — `.map(f)` type-checks but `f` never runs)
6. Remove `classify_yielded` ordering hacks (effects-before-programs)
7. Verify all tests pass

### Phase D: Cleanup — all items MUST be removed, no "after migration" hedge
1. ~~Remove Python CESK v1 and v3~~ **DONE** — `doeff/cesk/` directory deleted.
2. **REMOVE `Effect` enum and string-based `classify_yielded`** [Rev 8]:
   - `effect.rs`: Delete `Effect` enum. Replace with `#[pyclass(frozen)]` structs
     for all Rust-handled effects (Get, Put, Ask, Tell, Modify, Spawn, Gather, Race,
     CreatePromise, CompletePromise, FailPromise, CreateExternalPromise, TaskCompleted).
   - `pyvm.rs`: Delete ~300 lines of `match type_str { ... }`. Replace with
     single isinstance check: `is_effect_base(obj)` → `Yielded::Effect(obj)`.
   - `handler.rs`: `can_handle` and `start` receive `&Bound<'_, PyAny>` (not `Effect`).
   - `vm.rs`: `Yielded::Effect(Py<PyAny>)`, `DispatchContext.effect: Py<PyAny>`,
     `start_dispatch(py, effect: Py<PyAny>)`.
   - All handler impls: downcast in `start()` via `effect.downcast::<PyGet>()` etc.
3. **REMOVE deprecated Python effect aliases and compat shims:**
   - `effects/spawn.py`: `Promise.complete()`, `Promise.fail()`, `Task.join()` — DELETE
   - `effects/gather.py`: backwards compat alias — DELETE
   - `effects/future.py`: backwards compat alias — DELETE
   - `effects/scheduler_internal.py`: backwards compat aliases (2 blocks) — DELETE
   - `rust_vm.py`: `_LegacyRunResult` class + old PyVM fallback path — DELETE
   - `core.py`: entire compat re-export module — DELETE (or gut)
   - `_types_internal.py:35`: vendored type backward compat re-export — DELETE

---

## 9. Resolved Questions

1. **`Call(f, args, kwargs, metadata)` is a DoCtrl, not an Effect.**
   Like function calls in Koka/OCaml. The VM handles it directly: calls
   `f(*args, **kwargs)`, pushes the resulting generator frame with
   `CallMetadata`. No dispatch. Works for both DoThunks (no args) and kernel
   invocations (with resolved args). The metadata carries function_name,
   source_file, source_line — extracted by the driver with GIL.

2. **KPC is an Effect, not a DoCtrl.** Arg resolution scheduling is a
   handler concern. Sequential, concurrent, cached, retried — the handler decides.

3. **Auto-unwrap strategy is carried on the KPC effect.** Computed at `@do`
   decoration time from type annotations, stored on `KleisliProgramCall`.
   The KPC handler reads it to classify args.

4. **Default KPC handler resolves sequentially** using `Eval(expr, handlers)`
   per arg. `Eval` is a DoCtrl that creates an unstarted continuation
   with the given handler chain and evaluates the DoExpr within it. The caller
   is suspended and resumed with the result. No busy boundary issues because
   `Eval` uses explicit handlers, not `visible_handlers`.

5. **Arg resolution uses `Eval`, NOT direct effect yield or `Delegate`.**
   Direct effect yield would hit the busy boundary (KPC handler excluded from
   `visible_handlers`), breaking nested `@do` calls in args. `Delegate`
   advances within the same dispatch context — incompatible with multi-arg
   resolution. `Eval` creates a fresh scope with the full handler chain
   (captured via `GetHandlers` before the dispatch made anything busy).

6. **Sequential vs concurrent resolution is the handler's choice.** The default
   KPC handler uses `Eval` per-arg (sequential). A concurrent variant wraps
   args in `Gather` and uses a single `Eval`. Users swap handlers for
   different policies.

7. **Call stack is structural** (walked from segments/frames on demand), not
   tracked via push/pop. `GetCallStack` is a DoCtrl like `GetHandlers`.
   It returns `Vec<CallMetadata>` from `PythonGenerator` frames — pure Rust,
   no GIL needed.

8. **`Yielded::Program` is REMOVED (Rev 7).** The variant MUST be deleted from
    the Rust `Yielded` enum. All DoThunks go through `DoCtrl::Call` with
    `CallMetadata`. `classify_yielded` uses `CallMetadata::anonymous()` when
    the object carries no introspectable metadata. No fallback. No compat.

9. **DoExpr[T] is the universal composable base (Rev 6).**
   The old design had `EffectBase(ProgramBase)` — all effects inherited
   `to_generator()`. The new design has `DoExpr[T]` as the root:

   - `DoExpr[T]`: root — yieldable + composable (map, flat_map, +, pure)
   - `Program[T]`: user-facing alias for `DoExpr[T]`
   - `DoThunk[T]`: `DoExpr` + `to_generator()` (VM runs directly)
   - `Effect[T]`: `DoExpr` + handler dispatch
   - `DoCtrl[T]`: `DoExpr` + VM control instructions

   Every DoExpr produces a value T, so every DoExpr supports `.map()`.
   There is no non-composable yieldable — if it returns T, you can compose it.
   `Transfer` is `DoCtrl[Never]` (composable vacuously).

10. **Naming conventions (Rev 6).** `DoExpr`, `DoThunk`, `DoCtrl` use the
    `Do-` prefix (framework-internal concepts). `Program` and `Effect` are
    unprefixed (user-facing). `Program = DoExpr` is a type alias.

11. **run() requires explicit KPC handler (Rev 5).** The KPC handler is not
    auto-installed. If a KPC is dispatched with no handler, the VM errors.
    Users provide it via presets or explicit handler list.

12. **DoExpr.map() uniformly returns DerivedProgram / DoThunk (Rev 6).**
     `.map()` on ANY DoExpr (Effect, DoCtrl, DoThunk) returns a DerivedProgram.
     No special cases. The thunk wraps the original DoExpr in a generator
     that yields it.

13. **Effects are opaque data — the VM is a dumb pipe (Rev 8).**
    The `Effect` enum in Rust (`Effect::Get { key }`, `Effect::Python(obj)`, etc.)
    is REMOVED. Effects flow through dispatch as `Py<PyAny>`. The VM does not
    inspect effect fields. `classify_yielded` does ONE isinstance check for
    EffectBase — no per-type arms, no string matching, no field extraction.
    Handlers downcast to concrete `#[pyclass]` types themselves. All Rust-handled
    effects (`Get`, `Put`, `Ask`, `Tell`, `Modify`, scheduler effects) are
    `#[pyclass(frozen)]` structs defined in Rust and exposed to Python.
    This is separation of concerns: classification is the classifier's job,
    effect handling is the handler's job.

---

## 10. Open Questions

1. ~~**Composition operators after separation**~~

   **RESOLVED (Rev 6)**: `.map()` on any DoExpr (including KPC) returns a
   `DerivedProgram` (DoThunk). The composition chain crosses from Effect to
   DoThunk, but the user only sees `Program[T]` (= `DoExpr[T]`). See Section 4.7.

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
   class DoExpr(Generic[T]):
       def map(self, f: Callable[[T], U]) -> DoExpr[U]:
           source = self
           def thunk():
               result = yield source   # yields original DoExpr → VM handles it
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
