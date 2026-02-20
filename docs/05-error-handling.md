# Error Handling

This chapter covers error handling in doeff using `RunResult`, the `Try` effect, and canonical `Ok`/`Err` result values.

## Table of Contents

- [RunResult Overview](#runresult-overview)
- [Ok/Err Result Values](#okerr-result-values)
- [Result Methods](#result-methods)
- [Pattern Matching](#pattern-matching)
- [Try Effect](#try-effect)
- [Try Composition Guarantees](#try-composition-guarantees)
- [Captured Traceback on Err](#captured-traceback-on-err)
- [Practical Patterns](#practical-patterns)

## RunResult Overview

`run()` and `async_run()` return a `RunResult[T]`.

```python
from doeff import Ask, Tell, default_handlers, do, run

@do
def program():
    name = yield Ask("name")
    yield Tell(f"hello {name}")
    return name.upper()

result = run(program(), handlers=default_handlers(), env={"name": "doeff"})

if result.is_ok():
    print(result.value)         # "DOEFF"
    print(result.result)        # Ok("DOEFF")
else:
    print(result.error)
```

`RunResult` exposes:

- `result`: canonical `Ok(value)` or `Err(error)`
- `value`: unwrap success value (raises on error)
- `error`: unwrap error (raises on success)
- `is_ok()` / `is_err()`: status checks

## Ok/Err Result Values

Use the canonical import path:

```python
from doeff import Ok, Err
```

`Ok` and `Err` values returned by doeff runtime surfaces (`RunResult.result`, `Try(...)`, task
completion payloads) are a unified Rust-backed Result implementation.

### Fields

- `Ok.value`: success payload
- `Err.error`: exception payload
- `Err.captured_traceback`: optional captured traceback object (or `None`)

### Truthiness

- `bool(ok_value)` is `True`
- `bool(err_value)` is `False`

## Result Methods

Use the Result API methods for transformations and fallbacks:

- `value_or(default)`: get the success value or a fallback
- `map(f)`: transform only the `Ok` value
- `flat_map(f)`: chain functions that already return `Ok`/`Err`

## Pattern Matching

`Ok` and `Err` support native Python pattern matching.

```python
from doeff import Err, Ok, Try, default_handlers, do, run

@do
def might_fail(x: int):
    if x < 0:
        raise ValueError("x must be non-negative")
    return x * 2

@do
def workflow(x: int):
    return (yield Try(might_fail(x)))

result = run(workflow(-1), handlers=default_handlers())

match result.value:
    case Ok(value=v):
        print(f"success: {v}")
    case Err(error=e):
        print(f"failure: {e}")
```

## Try Effect

`Try(sub_program)` catches exceptions from `sub_program` and returns `Ok`/`Err` so you can keep control flow explicit.

```python
from doeff import Err, Ok, Try, Tell, default_handlers, do, run

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

result = run(app("not-a-number"), handlers=default_handlers())
print(result.value)  # 0
```

## Try Composition Guarantees

### 1. No-Rollback Rule

`Try(...)` catches exceptions as `Err(...)`, but it does not rollback effects that already happened.
State updates and `Tell(...)` log entries persist.

```python
from doeff import Get, Put, Try, default_handlers, do, run

@do
def mutate_then_fail():
    yield Put("counter", 1)
    raise ValueError("boom")

@do
def app():
    yield Put("counter", 0)
    result = yield Try(mutate_then_fail())
    counter = yield Get("counter")
    return (result.is_err(), counter)  # (True, 1)

print(run(app(), handlers=default_handlers()).value)
```

### 2. Env Restoration with `Local`

`Try(Local(...))` still respects `Local` frame restoration: env overrides are scoped and restored on
both success and failure. This env restoration is independent from the no-rollback behavior for
state/log.

```python
from doeff import Ask, Local, Try, default_handlers, do, run

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

print(run(app(), handlers=default_handlers(), env={"key": "outer"}).value)
```

`Local(Try(...))` is the inverse nesting order: `Try` catches inside the `Local` scope first, then
`Local` restores the outer env when the scope exits.

```python
from doeff import Ask, Local, Try, default_handlers, do, run

@do
def local_safe_inner():
    caught = yield Try(failing_inner())
    still_inner = yield Ask("key")
    return (caught.is_err(), still_inner)  # (True, "inner")

@do
def app_local_safe():
    before = yield Ask("key")
    inside = yield Local({"key": "inner"}, local_safe_inner())
    after = yield Ask("key")
    return (before, inside, after)  # ("outer", (True, "inner"), "outer")

print(run(app_local_safe(), handlers=default_handlers(), env={"key": "outer"}).value)
```

Contrast: `Try(Local(...))` catches after `Local` unwinds; `Local(Try(...))` catches before unwind
while still inside the local env.

### 3. Nested `Try` Result Shape

A nested Try does not collapse wrappers. `Try(Try(x))` returns nested results.

```python
from doeff import Try, default_handlers, do, run

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

print(run(app(), handlers=default_handlers()).value)
```

## Captured Traceback on Err

`Err` includes `captured_traceback` for debugging context.

```python
from doeff import Try, default_handlers, do, run

@do
def boom():
    raise ValueError("boom")

@do
def app():
    return (yield Try(boom()))

safe_result = run(app(), handlers=default_handlers()).value

if safe_result.is_err():
    print(type(safe_result.error).__name__)  # ValueError
    print(safe_result.captured_traceback)    # traceback object or None
```

## Practical Patterns

### Explicit fallback

```python
from doeff import Try, default_handlers, do, run

@do
def fetch_primary():
    raise RuntimeError("primary unavailable")

@do
def fetch_with_fallback():
    first = yield Try(fetch_primary())
    if first.is_ok():
        return first.value
    return "fallback"

print(run(fetch_with_fallback(), handlers=default_handlers()).value)
```

### Keep failures visible

```python
from doeff import Try, default_handlers, do, run

@do
def validate(v: int):
    if v <= 0:
        raise ValueError("v must be positive")
    return v

@do
def flow(v: int):
    checked = yield Try(validate(v))
    return checked  # caller receives Ok(...) or Err(...)

out = run(flow(0), handlers=default_handlers()).value
print(out.is_err())  # True
```
