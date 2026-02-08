# SPEC-TYPES-001: DoExpr Type Hierarchy — Draft Spec

## Status: WIP Discussion Draft (Rev 10)

### Rev 10 changes — Binary type hierarchy: DoExpr = DoCtrl | Effect

- **DoThunk eliminated.** The type hierarchy collapses from three categories (DoThunk,
  Effect, DoCtrl) to two: **DoCtrl** (VM syntax) and **Effect** (handler-dispatched data).
  DoThunk was always lowered to `DoCtrl::Call` by `classify_yielded` — it never reached
  the VM step loop as a distinct category. This revision removes the fiction.
- **Generator-as-AST framing.** From the VM's perspective, calling `gen.__next__()` /
  `gen.send(value)` is parsing the next token from a lazy AST. Each yielded DoExpr is
  an expression node. The VM is the evaluator. The generator IS the program text.
  `yield expr` is `Bind(expr, λresult. rest_of_program)` — the free monad, concretely.
- **`Pure(value)` added as DoCtrl.** The literal/value node in the expression grammar.
  Every expression language needs a leaf node that evaluates to a value immediately.
  `PureProgram` was a DoThunk workaround (wrapping a value in a generator just to return
  it). `Pure` is zero-cost: VM delivers the value directly, no generator allocation.
- **`Call` takes `DoExpr` args.** `Call(f: DoExpr, args: [DoExpr], kwargs, meta)` — the
  VM evaluates `f`, evaluates each arg/kwarg sequentially left-to-right, then invokes the
  resolved callable. This is the correct default: deterministic, simple. The KPC handler
  can pre-resolve args in parallel (via `Gather` + `Eval`) and emit `Call` with `Pure`
  args — the VM's sequential eval becomes a no-op. This is what "KPC is user-space" means:
  the evaluation strategy for args is not baked into the VM.
- **`Map` and `FlatMap` added as DoCtrl.** Replace `DerivedProgram` (which was a DoThunk).
  `expr.map(f)` → `Map(source=expr, f)`. `expr.flat_map(f)` → `FlatMap(source=expr, f)`.
  VM evaluates: eval source, apply f (Map) or eval f(result) (FlatMap). No generator
  overhead for simple compositions.
- **DoCtrl = the syntax of the doeff language.** DoCtrl is the complete instruction set:
  `Pure`, `Call`, `Eval`, `Map`, `FlatMap`, `Handle`, `Resume`, `Transfer`, `Delegate`,
  introspection primitives. Effect is the only non-syntax DoExpr — opaque data dispatched
  to handlers. The doeff language is: **fixed syntax** (DoCtrl) + **extensible operations**
  (Effect). The VM evaluates the syntax. Handlers interpret the operations.
- **`classify_yielded` becomes binary.** Two `is_instance_of` checks: DoCtrlBase → VM
  processes directly. EffectBase → dispatched to handler stack. No third category.

### Rev 9 changes — KPC is a Rust `#[pyclass]`, auto-unwrap strategy moves to handler
- **`KleisliProgramCall` is a `#[pyclass(frozen, extends=PyEffectBase)]` struct** defined
  in Rust. Fields: `kleisli_source`, `args`, `kwargs`, `function_name`, `execution_kernel`,
  `created_at`. KPC is a proper EffectBase subclass — `classify_yielded` catches it via
  the EffectBase isinstance check with zero special-casing. The KPC handler downcasts
  to `PyRef<PyKPC>` and reads Rust-native fields directly. See SPEC-008 R11-A.
- **Auto-unwrap strategy is the handler's responsibility.** `_AutoUnwrapStrategy` is
  NOT stored on KPC. The KPC handler computes it from `kleisli_source` annotations at
  dispatch time. This decouples the effect (KPC) from the resolution policy — different
  KPC handlers can implement different strategies (sequential, concurrent, cached, etc.)
  without changing the KPC type.

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
`pure`). `Program[T]` is a user-facing alias for `DoExpr[T]`. Two subtypes:
`DoCtrl` (VM syntax — fixed semantics) and `Effect` (handler dispatch — open
semantics). See Section 1.1 for the binary type hierarchy.

---

## 1. Design Principles

### 1.0 Generator as lazy AST [R10]

From the VM's perspective, calling `gen.__next__()` / `gen.send(value)` is
**parsing the next token from a lazy AST**. Each yielded `DoExpr` is an
expression node. The VM is the evaluator. The generator IS the program text.

```
gen.__next__()  →  Expr node      (parse next token from AST)
VM evaluates    →  Value          (evaluate expression)
gen.send(value) →  next Expr node (parse next token, result in scope)
gen.send(value) →  StopIteration  (end of program)
```

This is the free monad, concretely:

```
yield expr  ≡  Bind(expr, λresult. rest_of_program)
```

Where `yield` emits the `expr` node and the generator's internal state
captures the continuation `λresult. ...`. The VM evaluates `expr`, feeds
the result back, and the generator produces the next node.

### 1.1 Binary type hierarchy: DoCtrl | Effect [R10]

`DoExpr[T]` is the expression node type — everything yieldable inside a `@do`
generator. The VM classifies each DoExpr into exactly **two categories**:

```
DoExpr[T]
  ├── DoCtrl[T]    -- syntax (VM evaluates directly)
  └── Effect[T]    -- data (dispatched to handlers)
```

- **DoCtrl**: The VM **knows how to evaluate** these. Fixed semantics.
  `Pure(42)` always delivers 42. `Call(f, args)` always evaluates args and
  invokes f. `Map(expr, f)` always applies f to the result. No user-defined
  behavior. DoCtrl is the **complete instruction set** of the doeff language.

- **Effect**: The VM **doesn't know** what these mean. `Get("x")` means nothing
  to the VM — it finds a handler and passes the opaque data through. The
  semantics come from the handler, not the VM. Effects are **extensible
  operations** — users define new effects by subclassing `EffectBase`.

The doeff language is: **fixed syntax** (DoCtrl) + **extensible operations**
(Effect). The VM is the evaluator for the syntax. Handlers are the interpreters
for the operations.

### 1.2 DoCtrl — the instruction set [R10]

DoCtrl is the complete expression grammar of the doeff language:

```
DoCtrl[T] ::=
    -- values
    | Pure(value: T)                               -- literal (leaf node)

    -- computation
    | Call(f: DoExpr, args: [DoExpr], kwargs, meta) -- function application
    | Eval(expr: DoExpr, handlers: [Handler])       -- scoped evaluation
    | Map(source: DoExpr[S], f: S → T)              -- functor map
    | FlatMap(source: DoExpr[S], f: S → DoExpr[T])  -- monadic bind

    -- handler scoping
    | Handle(handler, body: DoExpr[T])              -- WithHandler

    -- continuations
    | Resume(k, value)
    | Transfer(k, value)                            -- DoCtrl[Never]
    | Delegate(effect?)

    -- introspection
    | GetHandlers
    | GetCallStack
    | GetContinuation
    | CreateContinuation(expr, handlers)
    | ResumeContinuation(k, value)

    -- async escape
    | PythonAsyncSyntaxEscape(action)
```

**`Pure(value)`** — the literal node. Every expression language needs a leaf that
evaluates to a value immediately. Zero cost: VM delivers the value, no generator
allocation, no handler dispatch. Replaces `PureProgram` (which wrapped a value
in a generator just to return it — a workaround for not having a literal node).

**`Call(f, args, kwargs, metadata)`** — function application. The VM evaluates
`f` (a DoExpr), evaluates each arg/kwarg **sequentially left-to-right** (each is
a DoExpr), then invokes the resolved callable with resolved values. The callable
must return a generator; the VM pushes it as a frame with `CallMetadata`.

Two common patterns:
- **Generator entry (no args)**: `Call(Pure(gen_factory), [], {}, meta)` — evaluates
  Pure (trivial), invokes `gen_factory()`, pushes generator frame.
- **Kernel invocation (with resolved args)**: `Call(Pure(kernel), [Pure(v1), Pure(v2)], {}, meta)`
  — all args are Pure (already resolved by KPC handler), VM evals trivially, invokes
  `kernel(v1, v2)`, pushes generator frame.

The metadata carries the caller's identity (function_name, source_file, source_line)
and optionally a reference to the `KleisliProgramCall` for rich introspection.
Metadata is extracted by the **driver** (with GIL) during `classify_yielded`, then
passed to the VM as part of the `Call` primitive.

**`Eval(expr, handlers)`** — scoped evaluation. Evaluates a DoExpr in a **fresh
handler scope** with the explicit handler chain. Exists because handlers run in a
different scope than the callsite (busy boundary). The KPC handler uses `Eval` to
resolve args with the callsite's full handler chain, not the handler's own chain.

**`Map(source, f)` / `FlatMap(source, f)`** — composition nodes. Replace
`DerivedProgram` (which was a DoThunk wrapping a generator). `Map` evaluates
`source`, applies `f` to the result, delivers `f(result)`. `FlatMap` evaluates
`source`, calls `f(result)` to get a DoExpr, evaluates that DoExpr, delivers the
final result. No generator overhead for simple compositions.

Note: `Map` can be derived from `FlatMap` + `Pure`:
`x.map(f) ≡ x.flat_map(λv. Pure(f(v)))`. Having `Map` as a separate node is an
optimization — avoids `Pure` wrapping in the common `.map()` case.

### 1.3 Call is syntax, KleisliProgramCall is an effect

These are at different levels:

| Concept | Type | Who handles | Example |
|---------|------|-------------|---------|
| Evaluate args + invoke | `Call(f, args, kwargs, meta)` (DoCtrl) | VM directly | Sequential arg eval, push frame |
| Resolve @do func args | `KleisliProgramCall` (Effect) | KPC handler | `my_do_func(x, y)` |

The VM provides `Call` with sequential arg evaluation as the **correct default**.
The KPC handler exists to override this default — it can pre-resolve args in
parallel (via `Gather` + `Eval`) and emit a `Call` where all args are `Pure`
(already resolved), making the VM's sequential eval a no-op. This is what
"KPC is user-space" means: the evaluation strategy for args is not baked into
the VM.

```
// KPC handler pre-resolves in parallel:
1. GetHandlers → capture callsite handler chain
2. Eval(arg1, handlers), Eval(arg2, handlers) ... in parallel via Gather
3. Call(Pure(kernel), [Pure(resolved1), Pure(resolved2), ...], meta)
//                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
//                     args are Pure — VM evals trivially
```

### 1.4 Why KPC is an effect, not syntax

Arg resolution scheduling is a **handler concern**:

| Strategy | How |
|----------|-----|
| Sequential (default) | Let `Call`'s built-in sequential eval do it |
| Parallel | `Gather` + `Eval`, then emit `Call` with `Pure` args |
| Cached | Memoize resolved args, emit `Call` with `Pure` args |
| Selective | Annotation-based: only resolve some args |
| Mocked | Test handler that substitutes fake resolutions |

The handler decides. Different strategies for different contexts.
Users who want custom resolution install a different KPC handler.

### 1.5 DoExpr, Program, and composability

**`DoExpr[T]`** (root type): Everything yieldable inside a `@do` generator.
"A do-expression." Every DoExpr produces a value `T` when the VM runs it,
and every DoExpr is composable (`map`, `flat_map`, `pure`). There is
no yieldable thing that isn't composable — if it returns T, you can `.map()` it.

**`Program[T]`** = **`DoExpr[T]`** (alias): User-facing name for `DoExpr[T]`.
Users write `Program[T]` in type hints. Internally it's `DoExpr[T]`.

```python
class DoExpr(Generic[T]):
    """Root: anything yieldable in @do. Always composable."""
    def map(self, f: Callable[[T], U]) -> DoCtrl[U]:
        return Map(self, f)          # returns a DoCtrl node
    def flat_map(self, f: Callable[[T], DoExpr[U]]) -> DoCtrl[U]:
        return FlatMap(self, f)      # returns a DoCtrl node
    @staticmethod
    def pure(value: T) -> DoCtrl[T]:
        return Pure(value)           # returns a DoCtrl node

# User-facing alias
Program = DoExpr
```

`.map()` on **any** DoExpr (Effect or DoCtrl) returns a `Map` DoCtrl. The user
only sees `Program[T]` (= `DoExpr[T]`) — the concrete subtype doesn't matter.

```
DoExpr[T]  (= Program[T])    ← root: yieldable + composable
  │
  ├── DoCtrl[T]               ← VM syntax (fixed semantics)
  │   ├── Pure[T]
  │   ├── Call, Eval, Map, FlatMap
  │   ├── Handle (WithHandler), Resume, Transfer, Delegate
  │   └── GetHandlers, GetCallStack, ...
  │
  └── Effect[T]               ← handler dispatch (open semantics)
      ├── Ask, Get, Put, Tell, Modify, ...
      ├── Spawn, Gather, Race, ...
      ├── KleisliProgramCall
      └── user-defined effects
```

| Type | DoExpr | DoCtrl | Effect |
|------|--------|--------|--------|
| Pure(value) | yes | **yes** | no |
| Map(source, f) | yes | **yes** | no |
| Call(f, args, ...) | yes | **yes** | no |
| Handle(h, body) | yes | **yes** | no |
| KleisliProgramCall | yes | no | **yes** |
| Ask, Get, Put, ... | yes | no | **yes** |

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

### Proposed (correct) — DoExpr = DoCtrl | Effect [R10]

```
DoExpr[T]  (= Program[T])    ← root: yieldable + composable
  │
  ├── DoCtrl[T]               ← VM syntax (fixed semantics, VM evaluates directly)
  │   ├── Pure[T]             ← literal value (replaces PureProgram)
  │   ├── Call[T]             ← function application (args are DoExpr)
  │   ├── Eval[T]             ← scoped evaluation
  │   ├── Map[T]              ← functor (replaces DerivedProgram for .map())
  │   ├── FlatMap[T]          ← monad bind (replaces DerivedProgram for .flat_map())
  │   ├── Handle[T]           ← install handler (WithHandler)
  │   ├── Resume[T]           ← resume continuation
  │   ├── Transfer[Never]     ← tail-resume (composable vacuously)
  │   ├── Delegate            ← re-dispatch to outer handler
  │   ├── GetHandlers, GetCallStack, GetContinuation
  │   ├── CreateContinuation, ResumeContinuation
  │   └── PythonAsyncSyntaxEscape
  │
  └── Effect[T]               ← handler dispatch (open semantics)
      ├── Ask[T], Get[T], Put, Tell, Modify, ...
      ├── Spawn, Gather, Race, ...
      ├── KleisliProgramCall[T]
      └── user-defined effects
```

**DoThunk is eliminated [R10].** It was a surface-level concept that `classify_yielded`
immediately lowered to `DoCtrl::Call`. The VM never processed it as a distinct category.
Its concrete subtypes are replaced by DoCtrl nodes:
- `PureProgram(value)` → `Pure(value)` — DoCtrl
- `DerivedProgram(.map(f))` → `Map(source, f)` — DoCtrl
- `DerivedProgram(.flat_map(f))` → `FlatMap(source, f)` — DoCtrl
- `GeneratorProgram(gen_fn)` → `Call(Pure(gen_fn), [], {}, meta)` — DoCtrl

Every DoExpr is composable. `Program[T]` is a user-facing alias for `DoExpr[T]`.

The VM handles DoExprs through **two** paths:
- **DoCtrl path**: VM evaluates directly (no dispatch, no handler involvement)
- **Effect path**: dispatch through handler stack via `start_dispatch`

Both paths produce a value T, so both support `.map()`:

```python
@do
def fetch_user(id: int) -> Program[User]: ...

# Effects — yieldable and composable:
name_prog = fetch_user(1).map(lambda u: u.name)   # KPC.map → Map (DoCtrl)
upper_key = Ask("api_key").map(str.upper)          # Effect.map → Map (DoCtrl)

# DoCtrl — also composable:
result = Handle(h, prog).map(lambda x: x + 1)     # DoCtrl.map → Map (DoCtrl)
```

---

## 3. The KPC Handler

### 3.1 Architecture

```
User code yields KleisliProgramCall(f, [Ask("key"), fetch_user(42)], {})
         │
         ▼ dispatched as effect
   ┌──────────────────────────────────────────────────────┐
   │  KPC Handler (RustHandlerProgram)                    │
   │                                                       │
   │  1. Compute auto_unwrap_strategy from annotations    │
   │  2. Classify args: unwrap vs pass-as-is              │
   │  3. Resolve unwrap-marked args:                      │
   │     - DoExpr arg → yield Eval(arg, handlers)         │
   │       (parallel via Gather, or sequential per-arg)   │
   │     - Plain value → wrap as Pure(value)              │
   │  4. yield Call(Pure(kernel), [Pure(v1), ...], meta)  │
   │     (all args are Pure — VM eval is trivial)         │
   │  5. Resume(k, result)                                │
   └──────────────────────────────────────────────────────┘
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

The auto-unwrap strategy is computed by the **KPC handler** from `kleisli_source`
annotations at dispatch time [Rev 9]. The strategy is NOT stored on the KPC effect —
it is internal to the handler. This means different KPC handlers can implement
different classification policies without changing the KPC type.

**DO unwrap** (`should_unwrap = True`) when annotation is:
- Plain types: `int`, `str`, `dict`, `User`, etc.
- No annotation (default: unwrap)
- Any type that is NOT a Program/Effect family type

**DO NOT unwrap** (`should_unwrap = False`) when annotation is:
- `Program`, `Program[T]`
- `DoCtrl`, `DoCtrl[T]`
- `Effect`, `Effect[T]`
- `DoExpr`, `DoExpr[T]`
- Any subclass of `Effect` (e.g., custom effect types)
- Any subclass of `DoCtrl`
- `Optional[Program[T]]`, `Program[T] | None`, `Annotated[Program[T], ...]`

**String annotation handling** (for `from __future__ import annotations`):
- Supports quoted strings, `Optional[...]`, `Annotated[...]`, union `|`
- Matches normalized strings: `"Program"`, `"Program[...]"`, `"DoCtrl"`,
  `"DoCtrl[...]"`, `"Effect"`, `"Effect[...]"`, `"DoExpr"`, etc.

**Parameter kinds**:
- `POSITIONAL_ONLY`: indexed in `strategy.positional`
- `POSITIONAL_OR_KEYWORD`: indexed in both `strategy.positional` and `strategy.keyword`
- `KEYWORD_ONLY`: in `strategy.keyword`
- `VAR_POSITIONAL` (`*args`): single `strategy.var_positional` bool for all
- `VAR_KEYWORD` (`**kwargs`): single `strategy.var_keyword` bool for all

### 3.4 Arg resolution behavior

| Arg value | `should_unwrap` | Handler action |
|-----------|----------------|----------------|
| `DoExpr` instance (Effect or DoCtrl) | `True` | `yield Eval(arg, handlers)` → wrap result as `Pure(resolved)` |
| `DoExpr` instance (Effect or DoCtrl) | `False` | Pass the DoExpr object as-is (wrap in `Pure` for Call arg position) |
| Plain value (`int`, `str`, etc.) | either | Wrap as `Pure(value)` |

All args in the final `Call` are DoExpr nodes (typically `Pure` for resolved values).
The VM evaluates each `Pure` trivially — zero overhead.

### 3.5 Resolution strategies

The default KPC handler is a `RustHandlerProgram` that resolves args using
`Eval(expr, handlers)` — a control primitive that evaluates any DoExpr
in a fresh scope with the given handler chain. The handler first captures
the callsite handlers via `GetHandlers`, then uses `Eval` for each arg.

```
// Default KPC handler (sequential resolution):
fn start(effect: PyKPC, k_user: Continuation) -> RustProgramStep:
    handlers = yield GetHandlers()
    // Handler computes strategy from kleisli_source annotations [Rev 9]
    strategy = build_auto_unwrap_strategy(effect.kleisli_source)

    resolved_args = []
    for (idx, arg) in effect.args:
        if strategy.should_unwrap(idx) and is_do_expr(arg):
            value = yield Eval(arg, handlers)
            resolved_args.push(Pure(value))
        else:
            resolved_args.push(Pure(arg))

    metadata = extract_call_metadata(effect)
    result = yield Call(Pure(effect.kernel), resolved_args, resolved_kwargs, metadata)
    yield Resume(k_user, result)
```

**`Eval` semantics**: `Eval(expr, handlers)` is a `DoCtrl` that
atomically creates an unstarted continuation with the given handler chain
and evaluates the DoExpr within it. The caller (KPC handler) is suspended;
when the evaluation completes, the VM resumes the caller with the result.
Internally equivalent to `CreateContinuation` + `ResumeContinuation` but
as a single step.

The DoExpr can be any yieldable value:
- **DoCtrl** (Pure, Map, Call, ...): VM evaluates directly within the
  continuation's scope
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
fn start(effect: PyKPC, k_user: Continuation) -> RustProgramStep:
    handlers = yield GetHandlers()
    strategy = build_auto_unwrap_strategy(effect.kleisli_source)
    exprs_to_resolve = [arg for (idx, arg) if strategy.should_unwrap(idx)]
    results = yield Eval(Gather(*exprs_to_resolve), handlers)
    // merge resolved values back with non-unwrapped args
    metadata = extract_call_metadata(effect)
    result = yield Call(Pure(effect.kernel), merged_pure_args, merged_kwargs, metadata)
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

`KleisliProgramCall` (`PyKPC`) is a `#[pyclass(frozen, extends=PyEffectBase)]` struct
defined in Rust [Rev 9]. It carries:

| Field | Type | Purpose |
|-------|------|---------|
| `kleisli_source` | `Py<PyAny>` | Reference to originating `KleisliProgram` (has `original_func`, signature) |
| `args` | `Py<PyTuple>` | Positional arguments |
| `kwargs` | `Py<PyDict>` | Keyword arguments |
| `function_name` | `String` | Human-readable name for tracing |
| `execution_kernel` | `Py<PyAny>` | The actual generator function to call |
| `created_at` | `Py<PyAny>` | `EffectCreationContext` for call tree reconstruction |

**Note [Rev 9]**: `auto_unwrap_strategy` is NOT a field on KPC. The KPC handler
computes it from `kleisli_source` annotations at dispatch time. This decouples
the effect from the resolution policy.

### 4.7 Composition on any DoExpr returns DoCtrl [R10]

`.map()` and `.flat_map()` on ANY DoExpr (Effect or DoCtrl) return a `Map`
or `FlatMap` DoCtrl node. This is uniform — no special cases, no generator
overhead:

```python
mapped = my_program().map(lambda x: x + 1)
# → Map(source=KPC(...), f=lambda x: x+1)  (DoCtrl)

mapped = Ask("key").map(str.upper)
# → Map(source=Ask("key"), f=str.upper)  (DoCtrl)
```

The full composability chain:

```python
result = (
    fetch_user(42)              # KPC (Effect)
    .map(lambda u: u.name)      # Map (DoCtrl) wrapping KPC
    .map(str.upper)             # Map (DoCtrl) wrapping Map
)
# result is a Map(Map(KPC, ...), ...) — nested DoCtrl nodes
# VM evaluates: dispatch KPC → get user → apply .name → apply str.upper
user = yield result
```

Every intermediate result is a `DoCtrl` (therefore a `DoExpr`) — always
yieldable, always composable. The `.map()` crosses from Effect to DoCtrl,
but the user only sees `Program[T]`.

**VM evaluation of `Map`**:
```rust
DoCtrl::Map { source, f } => {
    // 1. Evaluate source (dispatch to handler if Effect, or eval if DoCtrl)
    let value = eval(source);
    // 2. Call f(value) — pure Python function call
    let result = f(value);
    // 3. Deliver result
    self.mode = Mode::Deliver(result);
}
```

**VM evaluation of `FlatMap`**:
```rust
DoCtrl::FlatMap { source, binder } => {
    // 1. Evaluate source
    let value = eval(source);
    // 2. Call binder(value) — must return a DoExpr
    let next_expr = binder(value);
    // 3. Evaluate the resulting DoExpr
    let result = eval(next_expr);
    // 4. Deliver result
    self.mode = Mode::Deliver(result);
}
```

No generator allocation. No frame push for simple compositions. The composition
is structural — the VM walks the DoCtrl tree.

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

### 5.4 Metadata extraction flow [R10]

```
User code yields DoExpr (KPC, Effect, or DoCtrl)
    │
    ▼ driver classify_yielded (GIL held)
    │
    ├─ DoCtrl detected → pass through to VM step loop
    │   (Call nodes carry their own CallMetadata)
    │
    ├─ KPC (Effect) detected → extract metadata WITH GIL:
    │   function_name = kpc.function_name
    │   source_file   = kpc.kleisli_source.original_func.__code__.co_filename
    │   source_line   = kpc.kleisli_source.original_func.__code__.co_firstlineno
    │   program_call  = Some(kpc_ref)
    │   → emit Yielded::Effect(kpc)  (dispatched to KPC handler)
    │   → KPC handler will emit Call with this metadata attached
    │
    ├─ Other Effect detected → emit Yielded::Effect(obj)
    │   (dispatched to handler stack, no Call metadata needed)
    │
    ▼ VM handles Call(f_expr, arg_exprs, kwargs, metadata):
    1. Evaluate f_expr → get callable (for Pure, this is immediate)
    2. Evaluate each arg_expr sequentially → get resolved values
    3. Invoke callable(*resolved_args, **resolved_kwargs)
    4. Push Frame::PythonGenerator { generator, started: false, metadata: Some(m) }
```

**Key design point**: Metadata extraction happens in the driver (with GIL),
not in the VM. This is consistent with SPEC-008's architecture — the driver
does all Python interaction, the VM stays GIL-free.

**Note [R10]**: With DoThunk eliminated, there is no "anonymous DoThunk →
Call with anonymous metadata" path. All Call nodes are emitted by handlers
(primarily the KPC handler) or by `classify_yielded` when it encounters a
legacy `GeneratorProgram` (which is a DoCtrl wrapping a generator factory).

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
// KPC handler (RustHandlerProgram) pseudo-code [Rev 9]:
fn start(py: Python<'_>, effect: &Bound<'_, PyAny>, k_user: Continuation) -> RustProgramStep {
    let kpc: PyRef<PyKPC> = effect.downcast()?;  // downcast to Rust-native PyKPC
    let metadata = CallMetadata {
        function_name: kpc.function_name.clone(),
        source_file: extract_source_file(py, &kpc.kleisli_source),
        source_line: extract_source_line(py, &kpc.kleisli_source),
        program_call: Some(effect.into_py(py)),
    };

    // Handler computes auto-unwrap strategy from kleisli_source annotations
    let strategy = build_auto_unwrap_strategy(py, &kpc.kleisli_source);

    // ... resolve args via Eval using strategy ...

    // Call kernel with resolved args and metadata
    RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Call {
        f: kpc.execution_kernel.clone(),
        args: resolved_args,
        kwargs: resolved_kwargs,
        metadata,
    }))
}
```

---

## 6. DoExpr Taxonomy [R10]

All yieldable values in `@do` generators are `DoExpr[T]` (= `Program[T]`).
The VM classifies each DoExpr into **two categories** and handles accordingly:

```
DoExpr Subtype       Examples                        Handled by
────────────────────────────────────────────────────────────────────
DoCtrl               Pure(value),                    VM directly
                     Call(f, args, kwargs, meta),
                     Eval(expr, handlers),
                     Map(source, f),
                     FlatMap(source, binder),
                     Handle (WithHandler), Resume,
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
```

Both subtypes are `DoExpr[T]`, therefore both are composable with `map`,
`flat_map`, etc. `Transfer` is `DoCtrl[Never]` — composable vacuously
(`.map(f)` type-checks but `f` never runs since Transfer aborts).

`KleisliProgramCall` is a regular Effect — it goes through the handler stack
like any other effect. The KPC handler is a user-space handler (default
provided as `RustHandlerProgram`), not a VM-internal component.

**DoThunk is eliminated [R10].** There is no third category. What was formerly
DoThunk is now DoCtrl:
- `PureProgram` → `Pure` (DoCtrl) — literal value
- `DerivedProgram` → `Map` / `FlatMap` (DoCtrl) — composition nodes
- `GeneratorProgram` → `Call(Pure(gen_fn), [], {}, meta)` (DoCtrl) — generator entry

---

## 7. Impact on classify_yielded (Rust VM) [R10]

With the binary DoExpr hierarchy, `classify_yielded` classifies each yielded
`DoExpr` into **two** handling paths. **Effects are opaque data — the
classifier does not inspect them.**

```
Phase 1: obj.is_instance_of::<DoCtrlBase>()?  → downcast to specific DoCtrl variant
Phase 2: obj.is_instance_of::<EffectBase>()?  → Yielded::Effect(obj)
Phase 3: else                                 → Yielded::Unknown
```

Two C-level pointer comparisons. No Python imports. No `getattr`. No
`hasattr("to_generator")`. No string matching. No third category.

Both bases (`DoCtrlBase`, `EffectBase`) are Rust `#[pyclass(subclass)]` types
(SPEC-008 R11-F). Concrete types extend their base: `#[pyclass(extends=EffectBase)]`
for Rust effects, normal `class MyEffect(EffectBase)` for Python user effects.
`is_instance_of` is a C-level type pointer check — no MRO walk, no Python overhead.

The classifier never reads `.key`, `.value`, `.items`, or any
effect-specific attribute. Effects are data; the handler reads them.

**DoThunkBase is eliminated [R10].** There is no `Phase 3: DoThunkBase` check.
What was formerly DoThunk is now DoCtrl — `Pure`, `Map`, `FlatMap`, and `Call`
nodes are all `DoCtrlBase` subtypes caught by Phase 1.

### 7.1 Separation of Concerns — Effects Are Data [Rev 8]

**Principle**: The VM is a dumb pipe for effects. It does not know what `Get`
means, what `Spawn` does, or what fields `KleisliProgramCall` has. It only
knows two things: DoCtrl and Effect. For effects, it finds a handler
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

**Performance via Rust base classes (R11-F)**: `EffectBase` and `DoCtrlBase`
are `#[pyclass(subclass)]` in Rust. This means `is_instance_of::<PyEffectBase>()`
is a C-level pointer comparison — no Python module import, no `getattr`, no MRO
walk. The current implementation does `py.import("doeff.types")?.getattr("EffectBase")`
on every call — that overhead is eliminated entirely.

CODE-ATTENTION:
- `pyvm.rs`: Delete entire `match type_str { ... }` block. Delete
  `is_effect_object()` with its Python import path. Replace classify_yielded
  with **two** `is_instance_of` checks: `PyDoCtrlBase`, `PyEffectBase`. (R11-F, R10)
- `effect.rs` (or new `bases.rs`): Add `PyEffectBase`, `PyDoCtrlBase`
  as `#[pyclass(subclass, frozen)]`. **No `PyDoThunkBase`** — DoThunk is
  eliminated [R10]. Add `PyPure`, `PyMap`, `PyFlatMap` as
  `#[pyclass(frozen, extends=PyDoCtrlBase)]`.
- `effect.rs`: Delete `Effect` enum. Replace with `#[pyclass(extends=PyEffectBase)]` structs.
  Add `PyKPC` as `#[pyclass(frozen, extends=PyEffectBase)]` with fields:
  `kleisli_source`, `args`, `kwargs`, `function_name`, `execution_kernel`, `created_at`. [Rev 9]
- `pyvm.rs`: Update DoCtrl pyclasses to use `extends=PyDoCtrlBase`.
- `vm.rs`: `Yielded::Effect(Py<PyAny>)` not `Yielded::Effect(Effect)`.
  Add `DoCtrl::Pure`, `DoCtrl::Map`, `DoCtrl::FlatMap` variants. Update
  `DoCtrl::Call` to take `Vec<DoExprArg>` (each arg is a DoExpr, not a Value). [R10]
- `handler.rs`: `can_handle` and `start` receive `&Bound<'_, PyAny>`.
- All handler impls: downcast in `start()`, not pre-parsed by classifier.
- KPC handler impl: downcast to `PyRef<PyKPC>`, compute auto-unwrap strategy
  from `kleisli_source` annotations at dispatch time. Strategy is handler-internal,
  NOT stored on KPC. Emit `Call` with `Pure`-wrapped resolved args. [Rev 9, R10]
- Python side: Delete Python-defined `EffectBase` and import the Rust base
  classes from `doeff_vm`. No transitional compatibility layer is allowed.
  Delete Python `KleisliProgramCall` dataclass — replace with `PyKPC` imported
  from `doeff_vm`. Delete `_AutoUnwrapStrategy` from KPC — it moves into the
  KPC handler implementation. [Rev 9]
  Delete `PureProgram`, `DerivedProgram`, `GeneratorProgram` as DoThunk subtypes —
  replace with `Pure`, `Map`, `FlatMap`, `Call` DoCtrl nodes. [R10]

---

## 8. Migration Path

### Phase A: Spec + Rust types
1. Finalize this spec (SPEC-TYPES-001) and update SPEC-008
2. Add `CallMetadata` struct in Rust VM
3. Add `metadata: Option<CallMetadata>` to `Frame::PythonGenerator`
4. Add `Call { f, args, kwargs, metadata }` as a `DoCtrl` variant
5. Add `Eval { expr, handlers }` as a `DoCtrl` variant
6. Add `GetCallStack` as a `DoCtrl` variant
7. Implement metadata extraction in driver's `classify_yielded` with **mandatory KPC effect dispatch**:
   KPC must classify as `Yielded::Effect(kpc)` and be handled by the KPC handler.
   Legacy GeneratorProgram objects classify as `DoCtrl::Call(Pure(gen_fn), ...)`.
8. **REMOVE `Yielded::Program`** — delete the variant from the Rust enum.
   `classify_yielded` is binary: DoCtrlBase → VM, EffectBase → handler. No fallback path.

### Phase B: Introduce binary DoExpr type hierarchy [R10]
1. Define `DoExpr[T]` as composable base (map, flat_map, pure)
2. Define `Program[T]` as user-facing alias for `DoExpr[T]`
3. Define `DoCtrl[T]` as `DoExpr` + VM syntax (replaces ControlPrimitive AND DoThunk)
4. Define `Effect[T]` as `DoExpr` + handler dispatch
5. **No `DoThunk[T]`** — eliminated. Its subtypes become DoCtrl nodes:
   - `PureProgram` → `Pure` (DoCtrl)
   - `DerivedProgram` → `Map` / `FlatMap` (DoCtrl)
   - `GeneratorProgram` → `Call(Pure(gen_fn), [], {}, meta)` (DoCtrl)
6. Add `Pure(value)` as DoCtrl variant — literal/value node
7. Add `Map(source, f)` and `FlatMap(source, binder)` as DoCtrl variants
8. Update `Call` to take `DoExpr` args: `Call(f: DoExpr, args: [DoExpr], kwargs, meta)`
   VM evaluates args sequentially left-to-right by default
9. Make `KleisliProgramCall` a `#[pyclass(frozen, extends=PyEffectBase)]` struct in Rust (`PyKPC`)
   with fields: `kleisli_source`, `args`, `kwargs`, `function_name`, `execution_kernel`, `created_at` [Rev 9]
10. Make all standard effects (Get, Put, Ask, ...) implement `Effect`
11. `.map()` on any DoExpr returns `Map(source, f)` (DoCtrl) — no generator overhead
12. `.flat_map()` on any DoExpr returns `FlatMap(source, f)` (DoCtrl) — no generator overhead
13. `DoExpr.pure(value)` returns `Pure(value)` (DoCtrl)
14. Implement default KPC handler as `RustHandlerProgram` — handler computes auto-unwrap
    strategy from `kleisli_source` annotations at dispatch time. Handler emits `Call` with
    `Pure`-wrapped resolved args [Rev 9, R10]
15. Update `classify_yielded` to **binary**: DoCtrlBase → VM, EffectBase → handler dispatch.
    No third category. No `DoThunkBase` check.
16. Update presets to include KPC handler
17. Update `@do` decorator — `KleisliProgram.__call__` constructs `PyKPC` (imported from `doeff_vm`)
18. Delete Python-side `_AutoUnwrapStrategy` from KPC — it moves into the KPC handler [Rev 9]
19. Remove transitional compatibility state: no Python dataclass KPC, no runtime base rebasing,
    no mixed old/new dispatch paths. Rust-side `PyKPC` + handler path is the single source of truth.

### Phase C: Complete separation (binary DoExpr replaces old ProgramBase/EffectBase) [R10]
1. Remove `EffectBase(ProgramBase)` inheritance
2. `Effect` becomes `DoExpr` subtype (composable, handler-dispatched)
3. `DoCtrl` replaces both `ControlPrimitive` AND `DoThunk` — the complete VM instruction set
4. Delete `DoThunk`, `PureProgram`, `DerivedProgram`, `GeneratorProgram` as separate types
5. `Transfer` is `DoCtrl[Never]` (composable vacuously — `.map(f)` type-checks but `f` never runs)
6. Remove `classify_yielded` ordering hacks (effects-before-programs) — binary check is sufficient
7. Remove `to_generator()` protocol entirely — no DoExpr has this method
8. Verify all tests pass

### Phase D: Cleanup — all items MUST be removed, no "after migration" hedge
1. ~~Remove Python CESK v1 and v3~~ **DONE** — `doeff/cesk/` directory deleted.
2. **REMOVE `Effect` enum and string-based `classify_yielded`** [Rev 8]:
   - `effect.rs`: Delete `Effect` enum. Replace with `#[pyclass(frozen)]` structs
     for all Rust-handled effects (Get, Put, Ask, Tell, Modify, Spawn, Gather, Race,
     CreatePromise, CompletePromise, FailPromise, CreateExternalPromise, TaskCompleted).
     Add `PyKPC` as `#[pyclass(frozen, extends=PyEffectBase)]` [Rev 9].
   - `pyvm.rs`: Delete ~300 lines of `match type_str { ... }`. Replace with
     single isinstance check: `is_effect_base(obj)` → `Yielded::Effect(obj)`.
   - `handler.rs`: `can_handle` and `start` receive `&Bound<'_, PyAny>` (not `Effect`).
   - `vm.rs`: `Yielded::Effect(Py<PyAny>)`, `DispatchContext.effect: Py<PyAny>`,
     `start_dispatch(py, effect: Py<PyAny>)`.
   - All handler impls: downcast in `start()` via `effect.downcast::<PyGet>()` etc.
     KPC handler downcasts to `PyRef<PyKPC>` and computes auto-unwrap strategy from
     `kleisli_source` annotations [Rev 9].
    - `doeff/program.py`: Delete Python `KleisliProgramCall` dataclass — replace with
      `PyKPC` imported from `doeff_vm`. Delete `_AutoUnwrapStrategy` and
      `_build_auto_unwrap_strategy` from KPC — strategy computation moves into
      the KPC handler [Rev 9]. Delete `PureProgram`, `DerivedProgram`,
      `GeneratorProgram` — replace with `Pure`, `Map`, `FlatMap`, `Call` DoCtrl
      nodes imported from `doeff_vm` [R10].
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
    `CallMetadata`. No dispatch. `Call` takes DoExpr args [R10] — the VM
    evaluates them sequentially, then invokes the callable. Works for generator
    entry (no args) and kernel invocations (with resolved Pure args). The
    metadata carries function_name, source_file, source_line — extracted by
    the driver with GIL.

2. **KPC is an Effect, not a DoCtrl.** Arg resolution scheduling is a
   handler concern. Sequential, concurrent, cached, retried — the handler decides.

3. **Auto-unwrap strategy is the handler's responsibility [Rev 9].** The KPC
   handler computes the strategy from `kleisli_source` annotations at dispatch
   time. It is NOT stored on the KPC effect. This decouples the effect from the
   resolution policy — different KPC handlers can implement different strategies
   (sequential, concurrent, cached, etc.) without changing the KPC type.

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
    the Rust `Yielded` enum. There is no DoThunk category [R10]. All former
    DoThunks are now DoCtrl nodes (`Pure`, `Map`, `FlatMap`, `Call`).
    `classify_yielded` is binary: DoCtrlBase → VM, EffectBase → handler.

9. **DoExpr[T] = DoCtrl[T] | Effect[T] — binary hierarchy (R10).**
   The old design had three categories (DoThunk, Effect, DoCtrl). The new
   design has two — DoCtrl (VM syntax) and Effect (handler data):

   - `DoExpr[T]`: root — yieldable + composable (map, flat_map, pure)
   - `Program[T]`: user-facing alias for `DoExpr[T]`
   - `DoCtrl[T]`: VM syntax — fixed semantics (Pure, Call, Eval, Map, FlatMap,
     Handle, Resume, Transfer, Delegate, introspection)
   - `Effect[T]`: handler dispatch — open semantics (user-extensible)

   DoThunk is eliminated. Its subtypes become DoCtrl nodes:
   `PureProgram` → `Pure`, `DerivedProgram` → `Map`/`FlatMap`,
   `GeneratorProgram` → `Call(Pure(gen_fn), ...)`.

   Every DoExpr produces a value T, so every DoExpr supports `.map()`.
   There is no non-composable yieldable — if it returns T, you can compose it.
   `Transfer` is `DoCtrl[Never]` (composable vacuously).

10. **Naming conventions (Rev 6, updated R10).** `DoExpr`, `DoCtrl` use the
    `Do-` prefix (framework-internal concepts). `Program` and `Effect` are
    unprefixed (user-facing). `Program = DoExpr` is a type alias. `DoThunk`
    is eliminated — there is no `Do-` prefixed thunk concept.

11. **run() requires explicit KPC handler (Rev 5).** The KPC handler is not
    auto-installed. If a KPC is dispatched with no handler, the VM errors.
    Users provide it via presets or explicit handler list.

12. **DoExpr.map() returns Map DoCtrl (R10).**
     `.map()` on ANY DoExpr (Effect or DoCtrl) returns a `Map(source, f)` DoCtrl
     node. `.flat_map()` returns a `FlatMap(source, binder)` DoCtrl node.
     No generator overhead. No DerivedProgram. The VM evaluates the Map node
     directly: eval source, apply f, deliver result.

13. **Effects are opaque data — the VM is a dumb pipe (Rev 8, updated Rev 9).**
    The `Effect` enum in Rust (`Effect::Get { key }`, `Effect::Python(obj)`, etc.)
    is REMOVED. Effects flow through dispatch as `Py<PyAny>`. The VM does not
    inspect effect fields. `classify_yielded` does ONE isinstance check for
    EffectBase — no per-type arms, no string matching, no field extraction.
    Handlers downcast to concrete `#[pyclass]` types themselves. All Rust-handled
    effects (`Get`, `Put`, `Ask`, `Tell`, `Modify`, `KleisliProgramCall`, scheduler
    effects) are `#[pyclass(frozen)]` structs defined in Rust and exposed to Python.
    `KleisliProgramCall` (`PyKPC`) extends `PyEffectBase` — it is caught by the
    single EffectBase isinstance check like any other effect. [Rev 9]
    This is separation of concerns: classification is the classifier's job,
    effect handling is the handler's job.

14. **Generator-as-AST: DoExpr nodes are expression tokens, not calls (R10).**
    From the VM's perspective, `gen.__next__()` / `gen.send(value)` parses the
    next token from a lazy AST. Each yielded DoExpr is an expression node. The
    VM is the evaluator. `yield expr` is `Bind(expr, λresult. rest)` — the free
    monad concretely. DoExpr IS Expr, not Call. `Call` is one specific Expr node
    (function application). Every DoExpr evaluation is the VM processing an
    expression — not every expression is a "call."

15. **DoThunk eliminated — binary hierarchy (R10).** DoThunk was a surface-level
    concept immediately lowered to `DoCtrl::Call` by `classify_yielded`. The VM
    never processed it as a distinct category. Removing it simplifies the type
    hierarchy from three categories to two (DoCtrl | Effect), reduces
    `classify_yielded` from three isinstance checks to two, and eliminates the
    `to_generator()` protocol entirely.

16. **`Pure(value)` is the literal node (R10).** Every expression grammar needs
    a leaf node. `PureProgram` was a DoThunk workaround — it wrapped a value in
    a generator just to return it. `Pure` evaluates to the value immediately:
    `VM sees Pure(42) → deliver 42`. Zero generator allocation.

17. **`Call` takes DoExpr args with sequential evaluation (R10).** `Call(f, args,
    kwargs, meta)` — `f` and each arg/kwarg are DoExpr nodes. The VM evaluates
    them sequentially left-to-right. This is the correct default. The KPC handler
    can pre-resolve args in parallel and emit `Call` with `Pure` args — the VM's
    sequential eval becomes a no-op. This is the precise meaning of "KPC is
    user-space": the arg evaluation strategy is not baked into the VM.

18. **`Map` and `FlatMap` as DoCtrl nodes replace DerivedProgram (R10).**
    `expr.map(f)` → `Map(source=expr, f)`. The VM evaluates: eval source, apply
    f, deliver result. No generator allocation for simple compositions. `Map` can
    be derived from `FlatMap` + `Pure` (`x.map(f) ≡ x.flat_map(λv. Pure(f(v)))`)
    but having `Map` as a separate node avoids the `Pure` wrapping overhead.

---

## 10. Open Questions

1. ~~**Composition operators after separation**~~

   **RESOLVED (R10)**: `.map()` on any DoExpr (including KPC) returns a
   `Map` DoCtrl node. The composition crosses from Effect to DoCtrl, but the
   user only sees `Program[T]` (= `DoExpr[T]`). See Section 4.7.

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

   **RESOLVED (R10)**: `Effect.map(f)` returns a `Map(source, f)` DoCtrl node.
   No generator. No DerivedProgram. No DoThunk.

   ```python
   class DoExpr(Generic[T]):
       def map(self, f: Callable[[T], U]) -> DoCtrl[U]:
           return Map(self, f)
       def flat_map(self, f: Callable[[T], DoExpr[U]]) -> DoCtrl[U]:
           return FlatMap(self, f)
   ```

   This applies uniformly to ALL DoExprs, including Effects and DoCtrl nodes.

   ```
   Ask("key").map(f)       → Map(Ask("key"), f)         (DoCtrl)
   fetch_user(42).map(f)   → Map(KPC(...), f)           (DoCtrl)
   Get("k").map(f)         → Map(Get("k"), f)           (DoCtrl)
   Pure(42).map(f)         → Map(Pure(42), f)           (DoCtrl)
   ```

   The VM evaluates `Map` directly: eval source → apply f → deliver result.
   Zero generator overhead for simple compositions.

---

## 11. Public API Test Requirements

All tests in this section exercise the **public API surface** (`from doeff import ...`) through
the `run()` / `async_run()` entrypoints. No tests may reach into `doeff_vm` internals, Rust
source files, or private modules. Tests live in `tests/public_api/`.

### 11.1 Type hierarchy (§1.1, §1.5, §2) [R10]

Tests MUST verify:

| ID | Requirement | Spec Section |
|----|-------------|-------------|
| TH-01 | `DoExpr` and `DoCtrl` are distinct classes; `DoCtrl` is a subclass of `DoExpr` | §1.1 |
| TH-02 | `EffectBase` is a subclass of `DoExpr` | §1.1 |
| TH-03 | `DoCtrl` and `EffectBase` are the only two direct subtype categories of `DoExpr` | §1.1 |
| TH-04 | `KleisliProgramCall` is an instance of `EffectBase` (not `DoCtrl`) | §1.1, §2 |
| TH-05 | `Pure`, `Map`, `FlatMap`, `Call` are instances of `DoCtrl` | §1.2, §2 |
| TH-06 | `Pure(value)` is a `DoCtrl` and a `DoExpr` | §1.2 |
| TH-07 | Effects created via `Ask()`, `Get()`, `Tell()`, `Put()` are `EffectBase` instances | §1.1 |
| TH-08 | Effects are NOT `DoCtrl` instances (binary separation enforced) | §2 |
| TH-09 | `Program` is an alias for `DoExpr` (user-facing name) | §1.5 |
| TH-10 | No `DoThunk` type exists — there is no third category | §2, R10 |
| TH-11 | No `to_generator()` method on any DoExpr subtype | §2, R10 |

### 11.2 Handler authoring protocol (§1.1, SPEC-008)

Tests MUST verify end-to-end through `run()`:

| ID | Requirement | Spec Section |
|----|-------------|-------------|
| HP-01 | Custom handler: `def handler(effect, k)` generator yielding `Resume(k, value)` handles an effect | §1.1 |
| HP-02 | Handler receives the original effect object (can read attributes) | §1.1 |
| HP-03 | Handler post-processes: `resume_value = yield Resume(k, value)` captures body result | §1.1 |
| HP-04 | Handler abandons continuation: returning without `Resume` short-circuits | §1.1 |
| HP-05 | Handler delegates: `yield Delegate()` forwards effect to outer handler | §1.1 |
| HP-06 | Nested `WithHandler`: inner handler intercepts before outer | §1.1 |
| HP-07 | Stateful handler: closure state accumulates across multiple effect dispatches | §1.1 |
| HP-08 | `WithHandler(handler=h, program=body)` installs handler for scope of `body` | §1.1 |
| HP-09 | Multiple effects in one body: handler invoked for each | §1.1 |
| HP-10 | Handler + built-in handlers (`state`, `reader`, `writer`): coexist in same `run()` | §1.1 |

### 11.3 KPC dispatch and auto-unwrap (§3)

Tests MUST verify through `run()`:

| ID | Requirement | Spec Section |
|----|-------------|-------------|
| KD-01 | `@do` function call creates a `KleisliProgramCall` (not executed immediately) | §4.1 |
| KD-02 | KPC is dispatched as an effect through the handler stack (requires KPC handler) | §1.2, §1.3 |
| KD-03 | `run(kpc, handlers=[])` fails (no KPC handler) | §9 Q11 |
| KD-04 | `run(kpc, handlers=default_handlers())` succeeds (default handlers include KPC) | §4.1 |
| KD-05 | Plain-typed args (`int`, `str`) auto-unwrap: DoExpr args are resolved before body | §3.3, §3.4 |
| KD-06 | `Program[T]`-annotated args are NOT unwrapped: DoExpr passed as-is | §3.3 |
| KD-07 | `Effect`-annotated args are NOT unwrapped: Effect passed as-is | §3.3 |
| KD-08 | Unannotated args default to auto-unwrap | §3.3 |
| KD-09 | Non-generator early return from `@do` function works | §4.2 |
| KD-10 | `@do` preserves `__name__`, `__doc__`, `__qualname__` | §4.3 |
| KD-11 | `@do` on class methods works (descriptor protocol via `__get__`) | §4.4 |
| KD-12 | Kleisli composition `>>` operator produces a composable pipeline | §4.5 |
| KD-13 | Nested `@do` calls: `@do` function calling another `@do` function resolves correctly | §3.5 |

### 11.4 Composition (§4.7) [R10]

Tests MUST verify:

| ID | Requirement | Spec Section |
|----|-------------|-------------|
| CP-01 | `effect.map(f)` returns a `Map` (DoCtrl), not an Effect | §4.7 |
| CP-02 | `kpc.map(f)` returns a `Map` (DoCtrl), not a KPC | §4.7 |
| CP-03 | `effect.flat_map(f)` returns a `FlatMap` (DoCtrl) | §4.7 |
| CP-04 | Composed effect runs end-to-end: `Ask("key").map(str.upper)` resolves correctly through `run()` | §4.7 |
| CP-05 | Composed KPC runs end-to-end: `my_func(x).map(f)` resolves correctly through `run()` | §4.7 |
| CP-06 | Chained composition: `effect.map(f).map(g)` composes correctly (nested Map nodes) | §4.7 |
| CP-07 | `flat_map` rejects non-DoExpr return from binder | §4.7 |
| CP-08 | `DoExpr.pure(value)` creates a `Pure` (DoCtrl) returning that value | §1.2, §1.5 |
| CP-09 | `Pure(42)` evaluates to 42 through `run()` — no generator overhead | §1.2 |
| CP-10 | `Map(source, f)` evaluates source then applies f through `run()` | §4.7 |

### 11.5 `run()` contract (SPEC-009)

Tests MUST verify:

| ID | Requirement | Spec Section |
|----|-------------|-------------|
| RC-01 | `run(prog)` with no handlers → effects raise unhandled error | SPEC-009 §1 |
| RC-02 | `run(prog, handlers=default_handlers())` installs state+reader+writer | SPEC-009 §1 |
| RC-03 | `RunResult.result` returns `Ok` or `Err` | SPEC-009 §2 |
| RC-04 | `isinstance(result.result, Ok)` works for successful runs | SPEC-009 §2 |
| RC-05 | `isinstance(result.result, Err)` works for failed runs | SPEC-009 §2 |
| RC-06 | `RunResult.value` extracts the success value | SPEC-009 §2 |
| RC-07 | `RunResult.raw_store` reflects final state | SPEC-009 §2 |
| RC-08 | `RunResult.error` returns the exception for failures | SPEC-009 §2 |
| RC-09 | Import paths: `from doeff import run, async_run, WithHandler, Resume, Delegate, Transfer, K` | SPEC-009 §8 |
| RC-10 | Import paths: `from doeff.handlers import state, reader, writer, scheduler` | SPEC-009 §8 |
| RC-11 | Import paths: `from doeff.presets import sync_preset, async_preset` | SPEC-009 §7 |

### 11.6 Type validation — rejection paths (SPEC-009 §12)

Every typed parameter in the public API MUST raise `TypeError` for wrong types.
Tests MUST verify **both** the happy path (valid input accepted) and the rejection
path (invalid input raises `TypeError` with informative message). No duck-typing,
no silent coercion, no deferred errors.

#### Entrypoints

| ID | Requirement | Spec Section |
|----|-------------|-------------|
| TV-01 | `run(42)` raises `TypeError` mentioning "DoExpr" and "int" | SPEC-009 §12 |
| TV-02 | `run("hello")` raises `TypeError` mentioning "str" | SPEC-009 §12 |
| TV-03 | `run(lambda: 42)` raises `TypeError` with hint "Did you mean @do?" | SPEC-009 §12 |
| TV-04 | `run(my_gen_func)` (uncalled) raises `TypeError` with hint "Did you mean to call it?" | SPEC-009 §12 |
| TV-05 | `run(my_gen_func())` (raw generator) raises `TypeError` with hint "Wrap with @do" | SPEC-009 §12 |
| TV-06 | `run(prog, handlers="not_a_list")` raises `TypeError` | SPEC-009 §12 |
| TV-07 | `run(prog, env="not_a_dict")` raises `TypeError` mentioning "dict" | SPEC-009 §12 |
| TV-08 | `run(prog, store=[1,2,3])` raises `TypeError` mentioning "dict" | SPEC-009 §12 |
| TV-09 | `run(prog, env=None)` is accepted (None is valid) | SPEC-009 §12 |
| TV-10 | `run(prog, store=None)` is accepted (None is valid) | SPEC-009 §12 |

#### Dispatch primitives — construction-time validation

| ID | Requirement | Spec Section |
|----|-------------|-------------|
| TV-11 | `Resume("not_k", 42)` raises `TypeError` mentioning "K" at construction | SPEC-009 §12 |
| TV-12 | `Resume(k, value)` with valid K is accepted | SPEC-009 §12 |
| TV-13 | `Transfer("not_k", 42)` raises `TypeError` mentioning "K" at construction | SPEC-009 §12 |
| TV-14 | `Delegate(42)` raises `TypeError` mentioning "EffectBase" | SPEC-009 §12 |
| TV-15 | `Delegate()` with no args is accepted | SPEC-009 §12 |
| TV-16 | `Delegate(Ask("key"))` with valid EffectBase is accepted | SPEC-009 §12 |

#### WithHandler — construction-time validation

| ID | Requirement | Spec Section |
|----|-------------|-------------|
| TV-17 | `WithHandler("not_callable", prog)` raises `TypeError` | SPEC-009 §12 |
| TV-18 | `WithHandler(handler, 42)` raises `TypeError` mentioning "DoExpr" | SPEC-009 §12 |
| TV-19 | `WithHandler(handler, prog)` with valid handler+program is accepted | SPEC-009 §12 |

#### @do decorator

| ID | Requirement | Spec Section |
|----|-------------|-------------|
| TV-20 | `@do` applied to a non-callable (e.g. `do(42)`) raises `TypeError` | SPEC-009 §12 |
| TV-21 | `@do` applied to a regular function (non-generator) is accepted (early return pattern) | SPEC-009 §12 |
| TV-22 | `@do` applied to a generator function is accepted | SPEC-009 §12 |

---

## References

- SPEC-008: Rust VM internals (handler stacking, busy boundary, visible_handlers)
- SPEC-009: Public API (Rev 7)
- SPEC-EFF-005: Concurrency effects
- `doeff/program.py`: Current _AutoUnwrapStrategy, _build_auto_unwrap_strategy,
  _annotation_is_program, _annotation_is_effect implementations
- `doeff/do.py`: Current DoYieldFunction / @do decorator
- `packages/doeff-vm/src/vm.rs`: Current Yielded::Program handling, StartProgram
- `packages/doeff-vm/src/pyvm.rs`: Current classify_yielded implementation
- `packages/doeff-vm/src/scheduler.rs`: Current Spawn/Gather/scheduler handler
- BasisResearch/effectful: `Operation.__apply__` as interceptable call effect (prior art)
