# SPEC-009: Rust VM Public API

## Status: Draft (Revision 6)

### Revision 6 Changelog

| Tag | Section | Change |
|-----|---------|--------|
| **R6-A** | §1 Entrypoints, §9 Migration | Clarified rust-vm-only public path: `doeff.run` / `doeff.async_run` are the supported entrypoints; legacy CESK `sync_run` presets are retired from top-level exports. |
| **R6-B** | §7 Scheduler | Removed compatibility coercion note for `WaitEffect`; typed scheduler classification is strict (malformed/unsupported scheduler forms are errors). |
| **R6-C** | §3 Program, §5 Handlers | Clarified KPC dispatch expectation: KleisliProgramCall must route through effect-handler pipeline, not direct call rewrite bypass. |
| **R6-D** | §3, §4 | `KleisliProgramCall` is a `#[pyclass(frozen, extends=PyEffectBase)]` struct in Rust (`PyKPC`). Auto-unwrap strategy NOT stored on KPC — handler computes from `kleisli_source` annotations at dispatch time. See SPEC-008 R11-A and SPEC-TYPES-001 Rev 9. |

### Revision 5 Changelog

| Tag | Section | Change |
|-----|---------|--------|
| **R5-A** | §7 scheduler | Fixed scheduler effects list to match SPEC-008 `SchedulerEffect` enum. Removed `Await` (Python-level asyncio bridge, not scheduler) and `Wait` (not a SchedulerEffect variant). Added `CreateExternalPromise`, `TaskCompleted`. |
| **R5-B** | §1 run() | Removed dangling reference to "ADR-13 in SPEC-008" (does not exist). Requirement stands on its own. |
| **R5-C** | §7, §9 | Clarified scheduler is user-space: the built-in scheduler is a reference implementation, not a framework-internal component. Users can and should provide their own scheduler handlers. |

### Revision 4 Changelog

| Tag | Section | Change |
|-----|---------|--------|
| **R4-A** | §2 RunResult | Clarified `Result[T]` type, what `raw_store` contains (state only, not env/logs). |
| **R4-B** | §3 Program | Clarified `Program[T]` is a `KleisliProgramCall`, not the generator itself. Added lifecycle. |
| **R4-C** | §4 Effects | Clarified `Modify(key, fn)` signature. Added effect vs primitive taxonomy. |
| **R4-D** | §5 Handlers | Clarified Resume/Transfer/Delegate return semantics. Added "what handlers can yield" section. |
| **R4-E** | §6 WithHandler | Fixed type signature (not `-> Effect`). |
| **R4-F** | §7 Standard Handlers | Clarified store/env initialization mechanism and writer log access. |

### Revision 3 Changelog

| Tag | Section | Change |
|-----|---------|--------|
| **R3-A** | Design Philosophy (new) | New section: Correctness-first foundation philosophy. |
| **R3-B** | §1 Entrypoints | Clarified: `run()` uses WithHandler nesting, not internal bypass. |
| **R3-C** | §6 WithHandler | Strengthened: `run()` is defined in terms of WithHandler (not independent). |
| **R3-D** | §11 Invariants | Added API-9 through API-12: structural equivalence, sentinel handlers, classify completeness, async semantics. |

## Summary

Define the public API that the Rust VM (SPEC-008) must expose so that
doeff subpackages can migrate from the Python CESK v3 interpreter.

This spec defines **what users import and call** — not VM internals.

```
┌───────────────────────────────────────────────────────┐
│  User code / subpackages                              │
│                                                       │
│  @do                                                  │
│  def my_program():                                    │
│      x = yield Get("x")                              │
│      yield Put("x", x + 1)                           │
│      return x + 1                                     │
│                                                       │
│  result = run(                                        │
│      my_program(),                                    │
│      handlers=[state, reader, writer, my_handler],    │
│      env={"key": "val"},                              │
│      store={"x": 0},                                  │
│  )                                                    │
│  result.result   # Ok(1)                              │
│  result.raw_store  # {"x": 1}                         │
└───────────────────┬───────────────────────────────────┘
                    │  immutable in → immutable out
                    ▼
┌───────────────────────────────────────────────────────┐
│  Public API  (this spec)                              │
│                                                       │
│  run / async_run          entrypoints                 │
│  RunResult[T]             output                      │
│  Handler protocol         Callable[[Effect, K], ...]  │
│  Standard effects         Get, Put, Ask, Tell, Modify │
│  Standard handlers        state, reader, writer, ...  │
│  Composition              WithHandler                 │
│  Dispatch primitives      Resume, Delegate, Transfer  │
└───────────────────┬───────────────────────────────────┘
                    │  implementation detail
                    ▼
┌───────────────────────────────────────────────────────┐
│  Rust VM internals  (SPEC-008)                        │
│  PyVM, step machine, dispatch, segments, arena        │
│  ← not exposed to users                              │
└───────────────────────────────────────────────────────┘
```

---

## 0. Design Philosophy [R3-A]

This is a **foundation layer**. Every design decision prioritizes semantic
correctness over convenience, performance, or "it works."

### Correct vs. Working

- **Working**: produces the right output for observed inputs.
- **Correct**: produces the right output *for the right structural reasons*.

A foundation must be correct because higher layers build on its structural
guarantees. A function that returns the right value through an incorrect
mechanism (wrong segment topology, wrong scope chain, wrong handler ordering)
is a **bug** — even if all current tests pass. Higher-level composition
patterns will eventually depend on the structure, and the incorrect mechanism
will silently break.

### Implications for This Spec

1. **`run()` is defined in terms of `WithHandler`** — it is not an independent
   entrypoint with its own handler-installation mechanism. It wraps the program
   in `WithHandler` nesting and runs the result. This is not an implementation
   suggestion — it is a **semantic requirement**. Any implementation that
   bypasses `WithHandler` violates the spec.

2. **Standard handlers are handlers** — `state`, `reader`, `writer` are not
   special-cased in dispatch. They are `Handler` values that users pass to
   `run()` or `WithHandler`. The VM may optimize their execution (Rust-native),
   but the installation path and dispatch semantics must be identical to
   user-defined Python handlers.

3. **No implicit behavior** — `run()` installs no handlers by default.
   There are no hidden effects, no auto-imported handlers, no magic.
   What the user passes is exactly what runs.

4. **Spec is the source of truth** — when code and spec disagree, the question
   is "which is wrong?" — not "does it work?". Fix the spec first if it is
   wrong, then fix the code.

---

## 1. Entrypoints

### run

```python
def run(
    program: Program[T],
    handlers: list[Handler] = [],
    env: dict[str, Any] = {},
    store: dict[str, Any] = {},
) -> RunResult[T]:
```

Runs `program` synchronously.  Returns a `RunResult` containing the
final value (or error) and the final store snapshot.

- `handlers` — Explicit list.  **No handlers are installed by default.**
  If the program yields `Get("x")` but no `state` handler is installed,
  the VM raises `UnhandledEffect`.
- `env` — Initial read-only environment.  Accessible via `Ask` effect.
- `store` — Initial mutable state.  Accessible via `Get`/`Put`/`Modify`.

Handlers are installed as nested `WithHandler` around the program,
outermost-first. **This is a semantic requirement, not an implementation
suggestion** (§0). [R3-B, R5-B]

```
handlers=[h0, h1, h2]  →  WithHandler(h0, WithHandler(h1, WithHandler(h2, program)))
```

`h2` sees effects first (innermost).  `h0` sees effects last (outermost).

The implementation must produce the same VM state (segments, scope chains,
handler visibility) as if the user had manually written the `WithHandler`
nesting. This means `run()` creates proper `PromptBoundary` segments,
body segments with correct `scope_chain`, and deterministic handler ordering.

### async_run

```python
async def async_run(
    program: Program[T],
    handlers: list[Handler] = [],
    env: dict[str, Any] = {},
    store: dict[str, Any] = {},
) -> RunResult[T]:
```

Same semantics as `run`, but awaits async escapes (I/O-bound effects
that must leave the VM to await a Python coroutine).

`async_run` must be a true `async def` that yields control to the event
loop — not a synchronous function wrapped in `asyncio.ensure_future()`.
The driver loop must use `await` to execute `PythonAsyncSyntaxEscape`
actions, yielding the thread between steps. [R3-D]

---

## 2. RunResult

```python
class RunResult(Protocol[T_co]):
    @property
    def result(self) -> Result[T_co]:
        """Ok(value) or Err(exception)."""
        ...

    @property
    def raw_store(self) -> dict[str, Any]:
        """Final store snapshot after execution."""
        ...
```

Convenience accessors:

```python
    @property
    def value(self) -> T_co:
        """Unwrap Ok or raise the Err."""
        ...

    @property
    def error(self) -> BaseException:
        """Get Err or raise ValueError if Ok."""
        ...

    def is_ok(self) -> bool: ...
    def is_err(self) -> bool: ...
```

`RunResult` is a protocol.  The concrete implementation is internal.

### Result[T] [R4-A]

`Result[T]` is a sum type from `doeff`:

```python
from doeff import Ok, Err

result.result  # Ok(1) or Err(ValueError("..."))
```

- `Ok(value)` — program returned successfully
- `Err(exception)` — program raised an exception

This is the doeff `Result` type (see SPEC-CESK-002), not a Rust `Result`.

### raw_store contents [R4-A]

`raw_store` contains **only state entries** — the key-value pairs managed by
the `state` handler via `Get`/`Put`/`Modify`.

```python
result = run(program, handlers=[state, reader, writer],
             store={"x": 0}, env={"key": "val"})

result.raw_store  # {"x": 1}  ← state only
#                    NOT env, NOT logs
```

- `env` is NOT in `raw_store` — it is read-only, the caller already has it.
- Logs from `Tell` are NOT in `raw_store` — they are handler-specific state.
  See §7 (writer) for log access.

`raw_store` is always populated, even when `result` is `Err`. It reflects the
store state at the point execution stopped.

---

## 3. Program and @do

A `Program[T]` is a value that, when executed, yields effects and returns `T`.

### Writing a Program

```python
@do
def counter():
    x = yield Get("count")
    yield Put("count", x + 1)
    yield Tell(f"counted {x + 1}")
    return x + 1
```

- `yield <effect>` — request something from the runtime, receive a value back
- `return <value>` — produce the final result (`T` in `Program[T]`)
- No `Pure` effect.  `return` is the only way to produce a final value.

### What @do Does [R4-B]

`@do` converts a generator function into a `Program` factory. Calling the
factory does NOT execute the body — it creates a deferred program descriptor:

```python
@do
def my_func(a: int, b: str):
    ...

# Calling the factory creates a Program[T] (a KleisliProgramCall)
program = my_func(42, "hello")

# The body has NOT run yet. It runs when passed to run() or yielded.
result = run(program, handlers=[state], store={})
```

### Program Lifecycle [R4-B]

```
@do def f(x):         ← generator function (not a Program)
    ...

f(42)                 ← KleisliProgramCall (this IS a Program[T])
                         body has NOT executed

run(f(42), ...)       ← VM calls to_generator() to get the actual generator
                         body starts executing
                         yields/returns flow through VM step machine
```

`Program[T]` is not the generator itself — it is a `KleisliProgramCall` (`PyKPC`,
a `#[pyclass(frozen, extends=PyEffectBase)]` struct [R6-D]) that wraps the
generator function and its arguments. KPC is an effect — it is dispatched to the
KPC handler, which resolves args and calls the kernel. This deferred execution is
what makes programs composable — they can be passed to `WithHandler`, `Resume`,
etc. without starting execution.

---

## 4. Effects

An effect is a value yielded from a `Program` to request an operation.

### Yield Taxonomy [R4-C]

A program can yield three categories of values. Each is handled differently:

```
Category             Examples                       What the VM does
─────────────────────────────────────────────────────────────────────
Effect               Get, Put, Ask, Tell, KPC,      Dispatched through handler stack
                     custom effects
Composition          WithHandler                    Creates new handler scope
Dispatch primitive   Resume, Delegate, Transfer     Controls dispatch (handler-only)
```

`KleisliProgramCall` (KPC) is an effect [R6-D] — it is a `#[pyclass(frozen,
extends=PyEffectBase)]` struct dispatched to the KPC handler. The handler
computes auto-unwrap strategy from `kleisli_source` annotations and resolves
args via `Eval`. See SPEC-TYPES-001 §3.

Effects are the only category dispatched to handlers. Composition and dispatch
primitives are processed directly by the VM.

### Standard Effects

```
Effect          Constructor                    Handler    Description
─────────────────────────────────────────────────────────────────────
Get             Get(key: str)                  state      Read from store
Put             Put(key: str, value: Any)      state      Write to store
Modify          Modify(key: str, fn: Callable) state      Transform store value
Ask             Ask(key: str)                  reader     Read from env
Tell            Tell(value: Any)               writer     Append to log
```

`Modify(key, fn)` calls `fn(old_value)` and stores the result. `fn` is a
Python callable with signature `(Any) -> Any`. [R4-C]

Standard effects are provided by the framework.  They only work when
the corresponding handler is installed.

### Custom Effects

Any class can be an effect:

```python
@dataclass
class MyEffect:
    payload: str

@do
def my_program():
    result = yield MyEffect("hello")
    return result
```

The effect is dispatched to the nearest handler that doesn't `Delegate`
it.  If no handler handles it → `UnhandledEffect` error.

---

## 5. Handlers

### Protocol

```python
Handler = Callable[[Effect, K], Program[T]]
```

A handler is a callable (typically `@do`-decorated) that receives:
- `effect` — the effect being dispatched
- `k` — the delimited continuation (opaque handle, see below)

And returns a `Program[T]` that yields dispatch primitives, effects, or
composition primitives. The handler's return value becomes the result of the
`WithHandler(...)` expression that installed it.

### K — Opaque Continuation Handle

`K` is an opaque object representing the suspended caller's continuation.
Users can:
- Pass `k` to `Resume(k, value)` or `Transfer(k, value)`
- Store `k` for later use (e.g., in a scheduler)

Users cannot:
- Inspect `k`'s contents
- Construct a `K` manually
- Call `k` directly

### Dispatch Primitives [R4-D]

These are **only usable inside a handler** during effect dispatch.
Yielding them outside a handler is an error (API-3).

```
Primitive              Description                    Handler continues?
───────────────────────────────────────────────────────────────────────
Resume(k, value)       Resume caller with value.      YES — handler gets
                       Call-resume semantics.          continuation's return
                                                      value back.

Delegate()             Pass effect to outer handler.  NO — handler is done.
                       "I don't handle this."

Delegate(effect)       Pass different effect to        NO — handler is done.
                       outer handler. Substitution.

Transfer(k, value)     Resume caller with value.      NO — handler is done.
                       Tail-resume semantics.          (abandoned)
```

#### Resume vs Transfer [R4-D]

Both resume the caller's continuation with a value. The difference is what
happens to the handler afterward:

```python
# Resume: handler CONTINUES after the continuation completes
@do
def my_handler(effect, k):
    result = yield Resume(k, 42)    # ← caller runs, returns a value
    # result = whatever the caller's program returned
    # handler can do more work here
    return result

# Transfer: handler is ABANDONED — control never returns here
@do
def my_handler(effect, k):
    yield Transfer(k, 42)           # ← caller runs, handler is gone
    # THIS CODE NEVER EXECUTES
```

Use `Resume` when the handler needs the continuation's result (most common).
Use `Transfer` for tail-position optimization or when the handler is done.

#### Delegate [R4-D]

`yield Delegate()` terminates the current handler and re-dispatches the
effect to the next outer handler. The handler does NOT continue after Delegate.

`yield Delegate(other_effect)` does the same but substitutes a different
effect. The outer handler sees `other_effect`, not the original.

### Example: Custom Handler

```python
@do
def cache_handler(effect, k):
    if isinstance(effect, CacheGet):
        cached = _lookup(effect.key)
        result = yield Resume(k, cached)
        return result
    elif isinstance(effect, CachePut):
        _store(effect.key, effect.value)
        result = yield Resume(k, None)
        return result
    else:
        yield Delegate()
```

### Handler Lifecycle

```
  Program yields effect
       │
       ▼
  VM finds innermost handler (via WithHandler nesting)
       │
       ▼
  handler(effect, k) is called           ← dispatch begins
       │
       ├─ yield Resume(k, value)         ← resume caller, handler waits
       │     caller runs to completion
       │     result = yield Resume(...)   ← handler gets result back
       │     handler continues
       │
       ├─ yield Delegate()               ← handler done, try outer handler
       │
       └─ yield Transfer(k, value)       ← handler done, caller resumes
```

### What Handlers Can Yield [R4-D]

Handlers are programs. They can yield anything a program can yield:

| What | Example | Dispatched to |
|------|---------|---------------|
| Dispatch primitive | `yield Resume(k, v)` | VM processes directly |
| Effect | `yield Tell("log msg")` | Outer handler stack |
| Composition | `yield WithHandler(h, prog)` | VM creates new scope |
| Nested program | `yield sub_program()` | VM runs sub-program |

Effects yielded by a handler are dispatched to handlers **outside** the
current handler's scope — never to the handler itself.

```python
@do
def logging_handler(effect, k):
    if isinstance(effect, MyEffect):
        yield Tell(f"handling {effect}")    # ← dispatched to outer writer
        result = yield Resume(k, effect.payload)
        return result
    else:
        yield Delegate()
```

### Handlers Can Compose with WithHandler

Since `WithHandler` is a composition primitive (not a dispatch primitive),
handlers can install sub-handlers:

```python
@do
def outer_handler(effect, k):
    if isinstance(effect, ComplexEffect):
        inner_result = yield WithHandler(
            handler=inner_handler,
            program=sub_program(effect),
        )
        result = yield Resume(k, inner_result)
        return result
    else:
        yield Delegate()
```

---

## 6. Composition: WithHandler

```python
WithHandler(handler: Handler, program: Program[T])
```

`WithHandler` is a **composition primitive** — not an effect, not a dispatch
primitive (see §4 taxonomy). Yielding it installs `handler` around `program`
and returns the program's result. [R4-E]

Usable from **any** `Program`:
- In user programs
- Inside handlers
- Arbitrarily nested

```python
@do
def my_program():
    # Install a handler around a sub-program
    result = yield WithHandler(
        handler=my_handler,
        program=sub_program(),
    )
    return result
```

`WithHandler` is the **definition** of `run()`'s handler installation.
`run()` is not an independent mechanism that happens to produce similar
results — it is literally WithHandler nesting (§0, §1). [R3-C]

```
run(program, [h0, h1, h2])

  is defined as:

WithHandler(h0,
    WithHandler(h1,
        WithHandler(h2,
            program)))
```

---

## 7. Standard Handlers

These are provided by the framework.  They handle the standard effects
but **must be explicitly installed** — `run()` does not install them.

Standard handlers are opaque `Handler` values. From the user's perspective,
they are identical to user-defined Python handlers — they are passed to
`run(handlers=[...])` or `WithHandler(handler=...)` and dispatched through
the same mechanism. The fact that they are Rust-optimized internally is an
**implementation detail** that must not affect semantics (API-10).

### state

Handles: `Get`, `Put`, `Modify`

Provides mutable key-value state.

```python
from doeff.handlers import state

result = run(my_program(), handlers=[state], store={"x": 0})
result.raw_store  # {"x": final_value}  ← final state after execution
```

**Initialization** [R4-F]: The `store={}` parameter in `run()` seeds the
state handler's backing store before execution. The `state` handler reads and
writes this store via `Get`/`Put`/`Modify`. After execution, the final state
is extracted into `RunResult.raw_store`.

If `state` handler is not installed, the `store={}` parameter is still
accepted but has no effect — no handler will read it.

### reader

Handles: `Ask`

Provides read-only environment.

```python
from doeff.handlers import reader

result = run(my_program(), handlers=[reader], env={"key": "value"})
```

**Initialization** [R4-F]: The `env={}` parameter in `run()` seeds the reader
handler's backing environment. The `reader` handler reads it via `Ask`.
Environment values are never mutated during execution (API-5).

### writer

Handles: `Tell`

Provides append-only log.

```python
from doeff.handlers import writer

result = run(my_program(), handlers=[state, writer], store={"x": 0})
```

**Log access** [R4-F]: Logs are NOT in `RunResult.raw_store`. Logs are
internal to the writer handler. To access logs, use a custom writer handler
that captures them, or use a preset that exposes logs through a convention.
The standard `writer` handler appends to an internal log that is not currently
exposed via `RunResult`.

### scheduler [R5-A, R5-C]

Handles: `Spawn`, `Gather`, `Race`, `CreatePromise`,
`CompletePromise`, `FailPromise`, `CreateExternalPromise`, `TaskCompleted`

Provides cooperative concurrency within a single `run()` call.

The built-in scheduler is a **reference implementation** — a user-space handler,
not a framework-internal component. Users can provide their own scheduler handler
that handles the same `SchedulerEffect` variants. The built-in scheduler has no
special dispatch path or privileged access to VM internals (API-10).

Note: `Await` is a Python-level asyncio bridge effect (see SPEC-EFF-005), NOT a
scheduler effect. It is handled by the `sync_await_handler` or
`python_async_syntax_escape_handler`, not by the scheduler.

```python
from doeff.handlers import scheduler
```

### Presets

Convenience bundles for common configurations:

```python
from doeff.presets import sync_preset, async_preset

# sync_preset = [scheduler (sync), state, reader, writer, ...]
# async_preset = [scheduler (async), state, reader, writer, ...]

result = run(my_program(), handlers=sync_preset, store={"x": 0})
```

### default_handlers() [Q9]

Public convenience function returning the standard handler bundle `[state, reader, writer]`.
Available as `from doeff import default_handlers`.

```python
from doeff import run, default_handlers

# Explicit handler installation (required — run() defaults to handlers=[])
result = run(my_program(), handlers=default_handlers(), store={"x": 0})
```

**Note**: `run()` defaults to `handlers=[]` (API-1). Users must explicitly
pass `default_handlers()` or construct their own handler list.

---

## 8. Imports

### User Code

```python
# Entrypoints
from doeff import run, async_run

# IMPORTANT: do NOT import runtime internals from doeff_vm in user code.
# doeff_vm is implementation-layer plumbing, not a public API surface.

# Decorator
from doeff import do

# Standard effects
from doeff.effects import Get, Put, Modify, Ask, Tell

# Composition
from doeff import WithHandler

# Standard handlers
from doeff.handlers import state, reader, writer, scheduler

# Presets
from doeff.presets import sync_preset, async_preset

# Result type
from doeff import RunResult
```

### Handler Authors

```python
# Everything above, plus dispatch primitives:
from doeff import Resume, Delegate, Transfer
```

---

## 9. What is NOT Exposed [R5-C]

The following are **implementation-layer types** of the Rust VM (SPEC-008).
User code and subpackages must not import or depend on them.

`doeff_vm` itself is implementation-layer. Public user imports must come from
`doeff`, `doeff.handlers`, `doeff.effects`, and `doeff.presets` only.

These are the Rust/PyO3 classes behind the public API — not the public API
itself.  For example, `scheduler` (the handler object from `doeff.handlers`)
is public; `PySchedulerHandler` (the Rust class that implements it) is not.

```
Type                     Why not exposed
─────────────────────────────────────────────────────────────────────
PyVM                     Rust VM wrapper — hidden behind run()/async_run()
RustStore                Use Get/Put/Modify effects instead
PyStore                  Use handler-level state instead
StateHandlerFactory      Behind `from doeff.handlers import state`
ReaderHandlerFactory     Behind `from doeff.handlers import reader`
WriterHandlerFactory     Behind `from doeff.handlers import writer`
PySchedulerHandler       Behind `from doeff.handlers import scheduler`
classify_yielded         Effect router inside driver loop
step_once / feed_*       Async driver protocol
Segment / Arena          Continuation storage
Mode / StepEvent         Step machine internals
DispatchContext          Dispatch state
```

To be clear: **the handler objects themselves are public**.  Users import and
pass them around freely (`handlers=[state, reader, writer, scheduler]`).
What is internal is the Rust implementation class behind each one.

**Scheduler is user-space** [R5-C]: The built-in scheduler is a user-space
handler — it is NOT a framework-internal component. It follows the same
`RustProgramHandler` trait as `state`, `reader`, and `writer`. Users can
replace it entirely with their own scheduler handler. The `PySchedulerHandler`
Rust type listed above is internal only because it is an implementation detail
of the built-in reference scheduler, not because the scheduler concept is
framework-internal.

---

## 10. Migration Surface

### What Changes for Subpackages

| Before (Python CESK) | After (Rust VM) |
|---|---|
| `from doeff.cesk.run import sync_run` | `from doeff import run` |
| `sync_run(prog, sync_handlers_preset)` | `run(prog, handlers=sync_preset)` |
| `result.value` | `result.value` (same) |
| `result.raw_store` | `result.raw_store` (same) |

### What Changes for Handler Authors

The handler protocol changes:

```python
# BEFORE (Python CESK):
# Handler = Callable[[EffectBase, HandlerContext], Program[CESKState | ResumeK]]

@do
def my_handler(effect, ctx):
    if isinstance(effect, MyEffect):
        return CESKState.resume_value(effect.value, ctx)
    else:
        result = yield effect  # re-raise
        return CESKState.resume_value(result, ctx)

# AFTER (Rust VM):
# Handler = Callable[[Effect, K], Program[T]]

@do
def my_handler(effect, k):
    if isinstance(effect, MyEffect):
        result = yield Resume(k, effect.value)
        return result
    else:
        yield Delegate()
```

Key differences:
- `ctx: HandlerContext` → `k: K` (opaque continuation handle)
- `CESKState.resume_value(v, ctx)` → `yield Resume(k, v)`
- re-yielding the effect → `yield Delegate()`
- `ResumeK(k=..., value=...)` → `yield Transfer(k, value)`
- No `CESKState`, no `HandlerContext`, no `Store` / `Environment` access

### What Does NOT Change

- `@do` decorator
- Effect classes (`Get`, `Put`, `Ask`, `Tell`, `Modify`, custom effects)
- `RunResult` protocol (`.result`, `.raw_store`, `.value`, `.is_ok()`)
- Program structure: yield effects, return values

### Subpackage Classification

| Tier | Packages | Migration Effort |
|------|----------|------------------|
| 0 — zero change | doeff-openai, doeff-gemini, doeff-openrouter, doeff-seedream, doeff-google-secret-manager, doeff-linter, doeff-indexer, doeff-test-target | None — only use `@do` + effects |
| 1 — import change | doeff-conductor | Change `sync_run` → `run` import |
| 2 — handler rewrite | doeff-agentic, doeff-agents, doeff-flow, doeff-preset | Rewrite handlers to new protocol |

---

## 11. Invariants

| ID | Invariant |
|----|-----------|
| API-1 | `run()` installs no handlers by default |
| API-2 | Yielding an effect with no matching handler raises `UnhandledEffect` |
| API-3 | `Resume`, `Delegate`, `Transfer` are only meaningful inside a handler during dispatch |
| API-4 | `WithHandler` is usable from any Program (user code or handler) |
| API-5 | `env` parameter is read-only (accessible via `Ask`, never mutated) |
| API-6 | `store` parameter is the initial state; final state is in `RunResult.raw_store` |
| API-7 | `RunResult` is immutable — a snapshot of the execution outcome |
| API-8 | All effects (standard and custom) are dispatched through the handler stack |
| API-9 | `run()` installs handlers via `WithHandler` nesting — structurally identical to manual nesting (§0, §1) [R3-D] |
| API-10 | Standard handlers (`state`, `reader`, `writer`) are opaque `Handler` values with no special dispatch path [R3-D] |
| API-11 | All effect types (standard + scheduler) must be classifiable — unknown effects are bugs, not graceful degradation [R3-D] |
| API-12 | `async_run()` must be a true `async def` — it must yield control to the event loop, not block the thread [R3-D] |

---

## References

- SPEC-008: Rust VM for Algebraic Effects (VM internals)
- SPEC-CESK-007: Segment-Based Continuation Architecture
- SPEC-CESK-002: RuntimeResult Protocol
