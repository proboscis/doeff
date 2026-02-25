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

This means `Intercept` (the existing Python-level mechanism) cannot implement cross-cutting concerns like "log every `Tell` in this subtree, regardless of who emits it." `WithIntercept` solves this by operating at the VM level, where it sees yields from handlers inside its scope.

## API

```python
from doeff import WithIntercept

WithIntercept(f, expr, types=None, mode=None)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `f` | `(effect) -> DoExpr` | *(required)* | Interceptor function. Receives matched effects, must return a `DoExpr` (typically the original effect unchanged, or a transformed replacement). |
| `expr` | `DoExpr` | *(required)* | The scoped program to observe. All yields within this subtree are candidates for interception. |
| `types` | `tuple[type, ...]` or `None` | `()` (empty tuple) | Effect or `DoCtrl` subclass types to filter on via `isinstance`. |
| `mode` | `"include"` or `"exclude"` or `None` | `"include"` | How `types` is interpreted. See **Type Filtering** below. |

**Return**: A `DoExpr` that, when interpreted, runs `expr` while routing matched yields through `f`.

## Basic Usage

The simplest use: observe every effect in a scope without changing anything.

```python
from dataclasses import dataclass
from doeff import EffectBase, WithIntercept, do, EffectGenerator, run, default_handlers, Tell, WriterTellEffect

observed: list[str] = []

def log_all(effect):
    """Interceptor that records effects, then passes them through unchanged."""
    observed.append(repr(effect))
    return effect  # return the original effect — no transformation

@do
def my_program() -> EffectGenerator[None]:
    yield Tell("hello")
    yield Tell("world")

result = run(
    WithIntercept(log_all, my_program(), types=(WriterTellEffect,), mode="include"),
    handlers=default_handlers,
)

# observed now contains repr strings for both Tell effects
```

The interceptor `log_all` sees each `Tell` as it passes through, records it, and returns the original effect so dispatch continues normally.

## Cross-Cutting Observation

This is the key property that distinguishes `WithIntercept` from `Intercept`.

When a handler inside the scope yields effects of its own, `WithIntercept` sees those too. This enables true cross-cutting observation — you get a complete trace of every effect in the subtree, not just the ones the user program directly yields.

```python
from dataclasses import dataclass
from doeff import (
    EffectBase, WithIntercept, WithHandler, Resume, Delegate,
    do, EffectGenerator, run, default_handlers, Tell, WriterTellEffect,
)

@dataclass(frozen=True)
class Ping(EffectBase):
    label: str

seen: list[str] = []

def observe_tells(effect):
    """Cross-cutting observer: sees Tell from ANY source."""
    if isinstance(effect, WriterTellEffect):
        seen.append(effect.value)
    return effect

def ping_handler(effect, k):
    """Handler that internally yields a Tell when it handles a Ping."""
    if isinstance(effect, Ping):
        yield Tell(f"handler:{effect.label}")           # <- this Tell is visible!
        return (yield Resume(k, f"handled:{effect.label}"))
    yield Delegate()

@do
def user_program() -> EffectGenerator[str]:
    yield Tell("from-user")
    result: str = yield Ping(label="foo")
    yield Tell("after-ping")
    return result

# WithIntercept wraps WithHandler — so f sees handler's yields
observed_program = WithIntercept(
    observe_tells,
    WithHandler(ping_handler, user_program()),
    types=(WriterTellEffect,),
    mode="include",
)

run(observed_program, handlers=default_handlers)

# seen == ["from-user", "handler:foo", "after-ping"]
#                        ^^^^^^^^^^^
#          This one comes from ping_handler, not user_program.
#          Intercept would miss it. WithIntercept catches it.
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
# Intercept everything EXCEPT Tell
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
from doeff import WithIntercept, WithHandler, Resume, Delegate

ctrl_log: list[str] = []

def log_ctrl(ctrl):
    ctrl_log.append(type(ctrl).__name__)
    return ctrl

# Observe only WithHandler and Resume control nodes
observed = WithIntercept(
    log_ctrl,
    WithHandler(my_handler, my_program()),
    types=(WithHandler, Resume),
    mode="include",
)
```

This is useful for debugging handler dispatch — you can see exactly when handlers are installed and when continuations are resumed.

## Effect Transformation

`f` can return a **different** effect than the one it received. The returned effect replaces the original in the dispatch pipeline.

```python
def redact_tells(effect):
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
def audit_interceptor(effect):
    """Interceptor that emits its own Tell for each observed effect."""
    yield Tell(f"audit: saw {type(effect).__name__}")
    return effect  # pass through the original

audited = WithIntercept(
    audit_interceptor,
    WithHandler(ping_handler, user_program()),
    types=(Ping,),
    mode="include",
)
```

When `audit_interceptor` sees a `Ping`, it yields a `Tell` (which goes through normal handler dispatch) and then returns the original `Ping` for continued processing.

## No Re-Entrancy

When `f` yields effects, those yields **skip the interceptor that invoked f**. This prevents infinite loops and matches the semantics of handler re-entrancy.

```python
seen_by_f: list[str] = []

def counting_interceptor(effect):
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
inner_seen: list[str] = []
outer_seen: list[str] = []

def inner_observer(effect):
    inner_seen.append(f"inner:{effect}")
    yield Tell("from-inner-observer")  # outer sees this, inner does not
    return effect

def outer_observer(effect):
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

The placement of `WithIntercept` relative to `WithHandler` determines what the interceptor sees.

### WithIntercept wrapping WithHandler

`f` sees handler-emitted effects because the handler is **inside** the observed scope:

```python
# f sees ping_handler's Tell yields
WithIntercept(
    f,
    WithHandler(ping_handler, user_program()),
    types=(WriterTellEffect,),
    mode="include",
)
```

### WithHandler wrapping WithIntercept

`f` does **not** see handler-emitted effects because the handler is **outside** the observed scope:

```python
# f does NOT see ping_handler's Tell yields
WithHandler(
    ping_handler,
    WithIntercept(
        f,
        user_program(),
        types=(WriterTellEffect,),
        mode="include",
    ),
)
```

In this arrangement, `f` only sees yields from `user_program`. The handler's `Tell("handler:foo")` originates outside the `WithIntercept` boundary.

### Rule of Thumb

If you need `f` to see a handler's internal effects, `WithIntercept` must be the **outer** wrapper. If you only care about user-level effects, either arrangement works.

## WithIntercept vs Intercept

| Aspect | `Intercept` | `WithIntercept` |
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

**When to use `Intercept`**: Simple effect substitution in user-level code where you don't need to see handler internals.

**When to use `WithIntercept`**: Cross-cutting observation, tracing, auditing, or any case where handler-emitted effects must be visible.

## Best Practices

### DO:

- **Use `WithIntercept` for observability** — tracing, logging, metrics, auditing. It was designed for cross-cutting concerns.
- **Filter with `types`** — narrow the interceptor to only the effects it cares about. An unfiltered interceptor on a hot path adds overhead.
- **Return the original effect** when you only want to observe. Unnecessary transformations make debugging harder.
- **Place `WithIntercept` outside `WithHandler`** when you need to see handler yields. This is the whole point of the primitive.
- **Keep interceptors simple** — an interceptor that yields many effects of its own becomes hard to reason about.

### DON'T:

- **Don't use `WithIntercept` as a handler replacement** — it does not consume effects. Effects always proceed through normal dispatch after `f` returns.
- **Don't rely on execution order between nested interceptors** for correctness — use them for observation, not orchestration.
- **Don't mutate shared state in `f` without synchronization** if the observed program uses `gather` for concurrency.
- **Don't use `exclude` mode with empty `types` in production** unless you genuinely need to intercept every single yield. It is useful for debugging but noisy at scale.
- **Don't forget the scoping rule** — if `WithIntercept` is inside `WithHandler`, it cannot see that handler's yields. This is the most common mistake.

## Summary

| What | How |
|------|-----|
| Observe all effects in a scope | `WithIntercept(f, expr, types=(), mode="exclude")` |
| Observe specific effect types | `WithIntercept(f, expr, types=(Tell, Ping), mode="include")` |
| Observe everything except certain types | `WithIntercept(f, expr, types=(Tell,), mode="exclude")` |
| See handler-emitted effects | Wrap `WithHandler` inside `WithIntercept` |
| Transform effects before dispatch | Return a different effect from `f` |
| Emit side effects during observation | `yield` inside `f` |
| Avoid infinite loops | Automatic — `f`'s yields skip its own interceptor |
| Compose multiple observers | Nest `WithIntercept` layers; each is independent |
