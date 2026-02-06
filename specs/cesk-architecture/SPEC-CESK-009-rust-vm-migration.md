# SPEC-CESK-009: Rust VM Public API

## Status: Draft (Revision 3)

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
suggestion** (§0, ADR-13 in SPEC-008). [R3-B]

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

---

## 3. Program and @do

A `Program[T]` is a generator that yields effects and returns `T`:

```python
@do
def counter():
    x = yield Get("count")
    yield Put("count", x + 1)
    yield Tell(f"counted {x + 1}")
    return x + 1
```

- `yield <effect>` — request something from the runtime, receive a value
- `return <value>` — produce the final result (`Program[T]` where `T` = type of value)
- No `Pure` effect.  `return` is the only way to produce a final value.

`@do` converts a generator function into a `Program` factory:

```python
@do
def my_func(a: int, b: str) -> ...:
    ...

program: Program[T] = my_func(42, "hello")  # call to get a Program instance
```

---

## 4. Effects

An effect is a value yielded from a `Program` to request an operation.

### Standard Effects

```
Effect          Constructor              Handler    Description
─────────────────────────────────────────────────────────────────
Get             Get(key: str)            state      Read from store
Put             Put(key: str, value)     state      Write to store
Modify          Modify(key: str, fn)     state      Transform store value
Ask             Ask(key: str)            reader     Read from env
Tell            Tell(value)              writer     Append to log
```

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

A handler is a callable that receives:
- `effect` — the effect being dispatched
- `k` — the delimited continuation (opaque handle)

And returns a `Program[T]` (a generator) that yields dispatch primitives.

### Dispatch Primitives

These are **only usable inside a handler** during effect dispatch:

```
Primitive              Description
─────────────────────────────────────────────────────────────────
Resume(k, value)       Resume the caller's continuation with value.
                       The caller's `yield SomeEffect(...)` receives
                       `value` as its result.

Delegate()             Pass the effect to the next outer handler.
                       "I don't handle this."

Delegate(effect)       Pass a different effect to the outer handler.
                       Effect substitution.

Transfer(k, value)     One-shot transfer to continuation.
                       Like Resume but consumes the continuation.
```

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
       ├─ yield Resume(k, value)         ← resume caller with value
       │     caller receives value from its `yield`
       │
       ├─ yield Delegate()               ← skip, try next outer handler
       │
       └─ yield Transfer(k, value)       ← one-shot resume
```

### Handlers Can Yield Effects

Handlers are programs themselves.  They can yield effects, which are
dispatched to *outer* handlers:

```python
@do
def logging_handler(effect, k):
    if isinstance(effect, MyEffect):
        yield Tell(f"handling {effect}")    # ← effect, handled by writer
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
WithHandler(handler: Handler, program: Program[T]) -> Effect
```

`WithHandler` is a composition primitive, usable from **any** `Program`:
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

Provides mutable key-value state.  Initialized from `run(..., store={})`.

```python
from doeff.handlers import state

result = run(my_program(), handlers=[state])
result.raw_store  # final state
```

### reader

Handles: `Ask`

Provides read-only environment.  Initialized from `run(..., env={})`.

```python
from doeff.handlers import reader

result = run(my_program(), handlers=[reader], env={"key": "value"})
```

### writer

Handles: `Tell`

Provides append-only log.  Logs are accessible via `RunResult`.

```python
from doeff.handlers import writer
```

### scheduler

Handles: `Spawn`, `Await`, `Gather`, `Race`, `CreatePromise`,
`CompletePromise`, `FailPromise`, `Wait`

Provides cooperative concurrency within a single `run()` call.

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

---

## 8. Imports

### User Code

```python
# Entrypoints
from doeff import run, async_run

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

## 9. What is NOT Exposed

The following are **implementation-layer types** of the Rust VM (SPEC-008).
User code and subpackages must not import or depend on them.

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

- SPEC-CESK-008: Rust VM for Algebraic Effects (VM internals)
- SPEC-CESK-007: Segment-Based Continuation Architecture
- SPEC-CESK-002: RuntimeResult Protocol
