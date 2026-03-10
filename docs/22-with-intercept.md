# WithIntercept — Cross-Cutting Effect Observation

`WithIntercept` lets you observe and transform **every** effect yielded inside a scope — including effects emitted by handlers themselves. This is the primitive you reach for when logging, tracing, or auditing needs to see the full picture, not just the user program's yields.

## The Problem

Handler-emitted effects are invisible to observers below the handler in the stack.

Consider a writer handler that consumes `Tell`, and a handler_A that internally yields `Tell("from A")`. An observer sitting below handler_A never sees that internal `Tell` — it travels **up** to the writer, bypassing everything below:

```
┌──────────────────────┐
│  writer (Tell)       │  <- consumes Tell
├──────────────────────┤
│  handler_A           │  <- internally yields Tell("from A") -> goes UP
├──────────────────────┤
│  printer (observer)  │  <- never sees handler_A's Tell
├──────────────────────┤
│  user_program        │  <- its Tell is seen by printer
└──────────────────────┘
```

The user program's `Tell` passes through the printer on its way up, so the printer sees it. But handler_A's `Tell` originates *above* the printer and goes further up — the printer is never in the path.

The legacy `Intercept` API was removed because it could not implement cross-cutting concerns like "log every `Tell` in this subtree, regardless of who emits it." `WithIntercept` solves this by operating at the VM level, where it sees yields from handlers inside its scope.

## API

```python
from doeff import WithIntercept

WithIntercept(f, expr, types=None, mode="include")
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `f` | `@do (effect: Effect) -> DoExpr` | *(required)* | Interceptor function. Receives matched effects, must return a `DoExpr` (typically the original effect unchanged, or a transformed replacement). |
| `expr` | `DoExpr` | *(required)* | The scoped program to observe. All yields within this subtree are candidates for interception. |
| `types` | `tuple[type, ...]` or `None` | `None` | Optional effect or `DoCtrl` subclass filter. `None` means no type filter. |
| `mode` | `"include"` or `"exclude"` | `"include"` | How `types` is interpreted when a filter is provided. See **Type Filtering** below. |

**Return**: A `DoExpr` that, when interpreted, runs `expr` while routing matched yields through `f`.

## Basic Usage

The simplest use: observe every effect in a scope without changing anything.

```python
from dataclasses import dataclass
from doeff import Effect, EffectBase, WithIntercept, do, EffectGenerator, run, default_handlers, Tell, WriterTellEffect

observed: list[str] = []

@do
def log_all(effect: Effect):
    """Interceptor that records effects, then passes them through unchanged."""
    observed.append(repr(effect))
    return effect  # return the original effect — no transformation

@do
def my_program() -> EffectGenerator[None]:
    yield Tell("hello")
    yield Tell("world")

result = run(
    WithIntercept(log_all, my_program(), types=(WriterTellEffect,), mode="include"),
    handlers=default_handlers(),
)

# observed now contains repr strings for both Tell effects
```

The interceptor `log_all` sees each `Tell` as it passes through, records it, and returns the original effect so dispatch continues normally.

## Cross-Cutting Observation

This is the key property that distinguishes `WithIntercept` from legacy interception wrappers.

When a handler inside the scope yields effects of its own, `WithIntercept` sees those too. This enables true cross-cutting observation — you get a complete trace of every effect in the subtree, not just the ones the user program directly yields.

```python
from dataclasses import dataclass
from doeff import (
    Effect,
    EffectBase,
    Resume,
    Tell,
    WithHandler,
    WithIntercept,
    WriterTellEffect,
    default_handlers,
    do,
    run,
    EffectGenerator,
)

@dataclass(frozen=True)
class Ping(EffectBase):
    label: str

seen: list[str] = []

@do
def observe_tells(effect: Effect):
    """Cross-cutting observer: sees Tell from ANY source."""
    if isinstance(effect, WriterTellEffect):
        seen.append(effect.message)
    return effect

@do
def ping_handler(effect: Ping, k: object):
    """Handler that internally yields a Tell when it handles a Ping."""
    yield Tell(f"handler:{effect.label}")           # <- this Tell is visible!
    return (yield Resume(k, f"handled:{effect.label}"))

@do
def user_program() -> EffectGenerator[str]:
    yield Tell("from-user")
    result: str = yield Ping(label="foo")
    yield Tell("after-ping")
    return result

# WithIntercept wraps WithHandler — so f sees handler's yields
observed_program = WithIntercept(
    observe_tells,
    WithHandler(handler=ping_handler, expr=user_program()),
    types=(WriterTellEffect,),
    mode="include",
)

run(observed_program, handlers=default_handlers())

# seen == ["from-user", "handler:foo", "after-ping"]
#                        ^^^^^^^^^^^
#          This one comes from ping_handler, not user_program.
#          WithIntercept catches it because interception is VM-scoped.
```

## Type Filtering

The `types` and `mode` parameters control which yields reach `f`.

### Include Mode (default)

Only effects matching one of the listed types fire `f`:

```python
# Only intercept Tell effects
WithIntercept(f, expr, types=(WriterTellEffect,), mode="include")
```

With an empty `types` tuple in include mode, **nothing matches** — `f` never fires:

```python
# f is never called (include + empty = match nothing)
WithIntercept(f, expr, types=(), mode="include")
```

### Exclude Mode

All effects fire `f` **except** those matching the listed types:

```python
# Observe everything EXCEPT Tell
WithIntercept(f, expr, types=(WriterTellEffect,), mode="exclude")
```

With an empty `types` tuple in exclude mode, **everything matches** — `f` fires on every yield:

```python
# f is called on every yielded effect (exclude + empty = match everything)
WithIntercept(f, expr, types=(), mode="exclude")
```

### Summary Table

| `mode` | `types` | What matches |
|--------|---------|-------------|
| `"include"` | `()` | Nothing |
| `"include"` | `(A, B)` | Only `A` and `B` instances |
| `"exclude"` | `()` | Everything |
| `"exclude"` | `(A, B)` | Everything except `A` and `B` |

The `isinstance` check applies to the yielded value, so subclass relationships work as expected. Filtering on `WriterTellEffect` matches `Tell` because `Tell` is a subclass of `WriterTellEffect`.

## Intercepting Control Flow

Type filtering works on `DoCtrl` types too, not just `Effect` subclasses. This lets you observe structural control flow — handler installations, resumptions, and delegations.

```python
from doeff import Effect, WithIntercept, WithHandler, Resume, Delegate, do

ctrl_log: list[str] = []

@do
def log_ctrl(effect: Effect):
    ctrl_log.append(type(effect).__name__)
    return effect

# Observe only WithHandler and Resume control nodes
observed = WithIntercept(
    log_ctrl,
    WithHandler(handler=my_handler, expr=my_program()),
    types=(WithHandler, Resume),
    mode="include",
)
```

This is useful for debugging handler dispatch — you can see exactly when handlers are installed and when continuations are resumed.

## Effect Transformation

`f` can return a **different** effect than the one it received. The returned effect replaces the original in the dispatch pipeline.

```python
from doeff import Effect, Tell, WithIntercept, WriterTellEffect, do

@do
def redact_tells(effect: Effect):
    """Replace Tell payloads with a redacted version."""
    if isinstance(effect, WriterTellEffect):
        return Tell("[REDACTED]")
    return effect

redacted = WithIntercept(
    redact_tells,
    user_program(),
    types=(WriterTellEffect,),
    mode="include",
)
```

After `redact_tells` runs, the handler stack sees `Tell("[REDACTED]")` instead of the original payload. The user program is unaware — it still yields normally.

## Effectful Interceptors

The interceptor `f` can itself yield effects. This makes it possible to perform logging, metrics collection, or other side effects during observation.

```python
from doeff import Effect, Tell, do

@do
def audit_interceptor(effect: Effect):
    """Interceptor that emits its own Tell for each observed effect."""
    yield Tell(f"audit: saw {type(effect).__name__}")
    return effect  # pass through the original

audited = WithIntercept(
    audit_interceptor,
    WithHandler(handler=ping_handler, expr=user_program()),
    types=(Ping,),
    mode="include",
)
```

When `audit_interceptor` sees a `Ping`, it yields a `Tell` (which goes through normal handler dispatch) and then returns the original `Ping` for continued processing.

## No Re-Entrancy

When `f` yields effects, those yields **skip the interceptor that invoked f**. This prevents infinite loops and matches the semantics of handler re-entrancy.

```python
from doeff import Effect, Tell, WithIntercept, do

seen_by_f: list[str] = []

@do
def counting_interceptor(effect: Effect):
    """Yields a Tell, but that Tell does NOT re-enter this interceptor."""
    seen_by_f.append(repr(effect))
    yield Tell("from-interceptor")   # <- this Tell is NOT seen by counting_interceptor
    return effect

observed = WithIntercept(
    counting_interceptor,
    user_program(),
    types=(),
    mode="exclude",  # match everything
)
```

Even though `counting_interceptor` matches all types and yields a `Tell`, that `Tell` bypasses itself. Without this rule, the interceptor would see its own `Tell`, yield another `Tell`, see that one, and loop forever.

## Nesting

Multiple `WithIntercept` layers compose naturally. Each layer's yields skip only its own interceptor — other layers still see them.

```python
from doeff import Effect, Tell, WithIntercept, do

inner_seen: list[str] = []
outer_seen: list[str] = []

@do
def inner_observer(effect: Effect):
    inner_seen.append(f"inner:{effect}")
    yield Tell("from-inner-observer")  # outer sees this, inner does not
    return effect

@do
def outer_observer(effect: Effect):
    outer_seen.append(f"outer:{effect}")
    return effect

nested = WithIntercept(
    outer_observer,
    WithIntercept(
        inner_observer,
        user_program(),
        types=(), mode="exclude",
    ),
    types=(), mode="exclude",
)
```

Here:
- `inner_observer` sees effects from `user_program`
- `inner_observer`'s `Tell("from-inner-observer")` skips itself but is seen by `outer_observer`
- `outer_observer` sees effects from both `user_program` and `inner_observer`

## Scoping Rules

`WithIntercept` sees **all** effects in the causal chain — including effects emitted by handlers that are processing effects from within the interceptor's scope. The placement of `WithIntercept` relative to `WithHandler` does **not** affect visibility.

```python
# Both arrangements are equivalent — f sees ping_handler's Tell yields in either case.

# Arrangement A: WithIntercept outside
WithIntercept(
    f,
    WithHandler(handler=ping_handler, expr=user_program()),
    types=(WriterTellEffect,),
    mode="include",
)

# Arrangement B: WithIntercept inside
WithHandler(
    handler=ping_handler,
    expr=WithIntercept(f, user_program(), types=(WriterTellEffect,), mode="include"),
)
```

When `user_program` yields `Ping("x")`, the effect passes through the interceptor (filtered out by `types`) and reaches `ping_handler`. When `ping_handler` internally yields `Tell("handler:x")`, the interceptor sees it because the handler is processing an effect that originated from within the interceptor's scope.

The only effects `f` does **not** see are its own yields (re-entrancy guard) and effects from completely unrelated dispatch chains.

## WithIntercept vs Legacy Intercept (Removed)

| Aspect | Legacy `Intercept` (removed) | `WithIntercept` |
|--------|-------------|-----------------|
| Level | Python InterceptFrame | VM-level `DoCtrl` node |
| Sees handler yields | No | Yes |
| Type filtering | No (manual `isinstance` in transform) | Built-in `types` + `mode` |
| Transform signature | `(effect) -> Effect \| Program \| None` | `(effect) -> DoExpr` |
| Can yield effects | No | Yes (`f` is a generator) |
| Re-entrancy guard | N/A | `f`'s yields skip its own interceptor |
| Filters on DoCtrl types | No | Yes (`WithHandler`, `Resume`, `Delegate`) |
| Propagates to Gather/Spawn | Yes | Scoped to `expr` subtree |
| Composability | First non-None transform wins | All layers compose independently |

The legacy `Intercept` API is removed. Use `WithIntercept` for scoped interception.

## Best Practices

### DO:

- **Use `WithIntercept` for observability** — tracing, logging, metrics, auditing. It was designed for cross-cutting concerns.
- **Filter with `types`** — narrow the interceptor to only the effects it cares about. An unfiltered interceptor on a hot path adds overhead.
- **Return the original effect** when you only want to observe. Unnecessary transformations make debugging harder.
- **Place `WithIntercept` anywhere in the handler stack** — it sees handler-emitted effects regardless of nesting position.
- **Keep interceptors simple** — an interceptor that yields many effects of its own becomes hard to reason about.

### DON'T:

- **Don't use `WithIntercept` as a handler replacement** — it does not consume effects. Effects always proceed through normal dispatch after `f` returns.
- **Don't rely on execution order between nested interceptors** for correctness — use them for observation, not orchestration.
- **Don't mutate shared state in `f` without synchronization** if the observed program uses `gather` for concurrency.
- **Don't use `exclude` mode with empty `types` in production** unless you genuinely need to intercept every single yield. It is useful for debugging but noisy at scale.
- **Don't assume nesting order matters for visibility** — `WithIntercept` sees handler-emitted effects regardless of position. Nesting order only affects which interceptor layer sees effects first when multiple `WithIntercept` layers are composed.

## Summary

| What | How |
|------|-----|
| Observe all effects in a scope | `WithIntercept(f, expr, types=(), mode="exclude")` |
| Observe specific effect types | `WithIntercept(f, expr, types=(Tell, Ping), mode="include")` |
| Observe everything except certain types | `WithIntercept(f, expr, types=(Tell,), mode="exclude")` |
| See handler-emitted effects | Automatic — `WithIntercept` sees them regardless of nesting |
| Transform effects before dispatch | Return a different effect from `f` |
| Emit side effects during observation | `yield` inside `f` |
| Avoid infinite loops | Automatic — `f`'s yields skip its own interceptor |
| Compose multiple observers | Nest `WithIntercept` layers; each is independent |
