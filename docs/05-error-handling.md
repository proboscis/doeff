# Error Handling

This chapter covers error handling in doeff using the `Try` effect and canonical `Ok`/`Err` result values.

[![A narrated video, about two minutes: run returns the value or raises the exception as it is, Ok and Err as values, Try as an effect that try_handler answers by running the sub-program and resuming the caller with Err, the three Try composition guarantees, and the traceback kept on the exception inside Err](assets/videos/error-handling.jpg)](assets/videos/error-handling.mp4)

## Table of Contents

- [Run Returns Raw Values](#run-returns-raw-values)
- [Ok/Err Result Values](#okerr-result-values)
- [Pattern Matching](#pattern-matching)
- [Try Effect](#try-effect)
- [Try Composition Guarantees](#try-composition-guarantees)
- [Captured Traceback on Err](#captured-traceback-on-err)
- [Practical Patterns](#practical-patterns)

## Run Returns Raw Values

`run(doexpr)` takes a single argument and returns the raw result value directly. There is no
`RunResult` wrapper.

For custom handler composition, call a Program -> Program handler installer directly, for example
`handler(program)`. `writer` keeps its log in state, so install `state()` outside it.

```python
from doeff import Ask, Tell, do, run
from doeff_core_effects.handlers import reader, state, writer

@do
def program():
    name = yield Ask("name")
    yield Tell(f"hello {name}")
    return name.upper()

prog = program()
prog = writer(prog)
prog = state()(prog)
prog = reader(env={"name": "doeff"})(prog)
result = run(prog)
# result is "DOEFF" directly — no wrapper
print(result)  # DOEFF
```

When an exception is raised inside the program, `run()` re-raises it directly. Use `Try` to
capture errors as `Ok`/`Err` values instead.

## Ok/Err Result Values

Use the canonical import path:

```python
from doeff import Ok, Err

print(Ok(5), Err(ValueError("boom")))  # Ok(5) Err(ValueError('boom'))
```

`Ok` and `Err` values are returned by `Try(...)` and task completion payloads. They are a Rust-backed
Result implementation.

### Fields

- `Ok.value`: success payload
- `Err.error`: exception payload
- `Err.captured_traceback`: optional captured traceback object (`None` for errors caught by
  `try_handler` — see [Captured Traceback on Err](#captured-traceback-on-err))

### Truthiness

- `bool(ok_value)` is `True`
- `bool(err_value)` is `False`

### Type checking

Use `isinstance` to check the result type:

```python
from doeff import Ok, Err

result = Ok(42)

if isinstance(result, Ok):
    print(result.value)  # 42
elif isinstance(result, Err):
    print(result.error)
```

## Pattern Matching

`Ok` and `Err` support native Python pattern matching.

```python
from doeff import Err, Ok, Try, do, run
from doeff_core_effects.handlers import try_handler

@do
def might_fail(x: int):
    if x < 0:
        raise ValueError("x must be non-negative")
    return x * 2

@do
def workflow(x: int):
    return (yield Try(might_fail(x)))

prog = workflow(-1)
prog = try_handler(prog)
result = run(prog)

match result:
    case Ok(value=v):
        print(f"success: {v}")
    case Err(error=e):
        print(f"failure: {e}")  # failure: x must be non-negative
```

## Try Effect

`Try(sub_program)` catches exceptions from `sub_program` and returns `Ok`/`Err` so you can keep control flow explicit.

`Try` is handled by `try_handler` (from `doeff_core_effects.handlers`): install it around the
program that yields `Try`, like any other handler. Without it, `run()` raises
`UnhandledEffect`.

```python
from doeff import Err, Ok, Try, Tell, do, run
from doeff_core_effects.handlers import state, try_handler, writer

@do
def parse_count(raw: str):
    return int(raw)

@do
def app(raw: str):
    parsed = yield Try(parse_count(raw))

    match parsed:
        case Ok(value=count):
            yield Tell(f"parsed count={count}")
            return count
        case Err(error=exc):
            yield Tell(f"parse failed: {exc}")
            return 0

prog = app("not-a-number")
prog = try_handler(prog)
prog = writer(prog)
prog = state()(prog)
result = run(prog)
print(result)  # 0
```

## Try Composition Guarantees

### 1. No-Rollback Rule

`Try(...)` catches exceptions as `Err(...)`, but it does not rollback effects that already happened.
State updates and `Tell(...)` log entries persist.

```python
from doeff import Err, Ok, Get, Put, Try, do, run
from doeff_core_effects.handlers import state, try_handler

@do
def mutate_then_fail():
    yield Put("counter", 1)
    raise ValueError("boom")

@do
def app():
    yield Put("counter", 0)
    result = yield Try(mutate_then_fail())
    counter = yield Get("counter")
    return (isinstance(result, Err), counter)  # (True, 1)

prog = app()
prog = try_handler(prog)
prog = state()(prog)
print(run(prog))  # (True, 1)
```

### 2. Env Restoration with `Local`

`Try(Local(...))` still respects `Local` frame restoration: env overrides are scoped and restored on
both success and failure. This env restoration is independent from the no-rollback behavior for
state/log.

`Local` is handled by `local_handler`; it answers `Ask` for the overridden keys and passes the
other keys on to the outer `reader`.

```python
from doeff import Ask, Local, Try, do, run
from doeff_core_effects.handlers import local_handler, reader, try_handler

@do
def failing_inner():
    _ = yield Ask("key")
    raise ValueError("fail inside local")

@do
def app():
    before = yield Ask("key")
    _ = yield Try(Local({"key": "inner"}, failing_inner()))
    after = yield Ask("key")
    return (before, after)  # ("outer", "outer")

prog = app()
prog = local_handler(prog)
prog = try_handler(prog)
prog = reader(env={"key": "outer"})(prog)
print(run(prog))  # ('outer', 'outer')
```

`Local(Try(...))` is the inverse nesting order: `Try` catches inside the `Local` scope first, then
`Local` restores the outer env when the scope exits.

```python
from doeff import Ask, Err, Local, Try, do, run
from doeff_core_effects.handlers import local_handler, reader, try_handler

@do
def failing_inner():
    _ = yield Ask("key")
    raise ValueError("fail inside local")

@do
def local_safe_inner():
    caught = yield Try(failing_inner())
    still_inner = yield Ask("key")
    return (isinstance(caught, Err), still_inner)  # (True, "inner")

@do
def app_local_safe():
    before = yield Ask("key")
    inside = yield Local({"key": "inner"}, local_safe_inner())
    after = yield Ask("key")
    return (before, inside, after)  # ("outer", (True, "inner"), "outer")

prog = app_local_safe()
prog = local_handler(prog)
prog = try_handler(prog)
prog = reader(env={"key": "outer"})(prog)
print(run(prog))  # ('outer', (True, 'inner'), 'outer')
```

Contrast: `Try(Local(...))` catches after `Local` unwinds; `Local(Try(...))` catches before unwind
while still inside the local env.

### 3. Nested `Try` Result Shape

A nested Try does not collapse wrappers. `Try(Try(x))` returns nested results.

```python
from doeff import Ok, Err, Try, do, run
from doeff_core_effects.handlers import try_handler

@do
def succeeds():
    return 5

@do
def fails():
    raise ValueError("inner failure")

@do
def app():
    a = yield Try(Try(succeeds()))
    b = yield Try(Try(fails()))
    return (a, b)  # (Ok(Ok(5)), Ok(Err(ValueError(...))))

print(run(try_handler(app())))  # (Ok(Ok(5)), Ok(Err(ValueError('inner failure'))))
```

## Captured Traceback on Err

`Err` has a `captured_traceback` field, but `try_handler` leaves it `None`. The traceback of the
failure stays on the caught exception itself (`err.error.__traceback__`), as with any caught
Python exception.

```python
import traceback

from doeff import Ok, Err, Try, do, run
from doeff_core_effects.handlers import try_handler

@do
def boom():
    raise ValueError("boom")

@do
def app():
    return (yield Try(boom()))

safe_result = run(try_handler(app()))

if isinstance(safe_result, Err):
    print(type(safe_result.error).__name__)  # ValueError
    print(safe_result.captured_traceback)    # None
    frames = traceback.extract_tb(safe_result.error.__traceback__)
    print(frames[-1].name)  # boom
```

## Practical Patterns

### Explicit fallback

```python
from doeff import Ok, Err, Try, do, run
from doeff_core_effects.handlers import try_handler

@do
def fetch_primary():
    raise RuntimeError("primary unavailable")

@do
def fetch_with_fallback():
    first = yield Try(fetch_primary())
    match first:
        case Ok(value=v):
            return v
        case _:
            return "fallback"

print(run(try_handler(fetch_with_fallback())))  # fallback
```

### Keep failures visible

```python
from doeff import Ok, Err, Try, do, run
from doeff_core_effects.handlers import try_handler

@do
def validate(v: int):
    if v <= 0:
        raise ValueError("v must be positive")
    return v

@do
def flow(v: int):
    checked = yield Try(validate(v))
    return checked  # caller receives Ok(...) or Err(...)

out = run(try_handler(flow(0)))
print(isinstance(out, Err))  # True
```
