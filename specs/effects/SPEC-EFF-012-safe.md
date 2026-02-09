# SPEC-EFF-012: Safe (Error-Tolerant Program Wrapper)

## Status: Implemented

## Summary

`Safe` is a program combinator that wraps a sub-program to catch exceptions
and return `Result[T]` (`Ok(value)` or `Err(exception)`) instead of raising.
It enables error-tolerant composition, particularly with `Gather` where
fail-fast behavior would otherwise discard partial results.

## Definition

```python
def Safe(sub_program: ProgramLike) -> Effect
```

`Safe` takes a `ProgramLike` (any `@do` generator, `KleisliProgramCall`, or
bare effect) and returns a new program that:
- On success: returns `Ok(value)`
- On exception: returns `Err(exception)`

The sub-program's effects are forwarded normally — only the final
success/failure is wrapped.

## Result Types

```python
@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T

@dataclass(frozen=True)
class Err:
    error: Exception
```

Both are importable from `doeff.types`.

## Implementation

`Safe` is a **Python-level program combinator**, not a VM-level effect.
It wraps the sub-program's execution kernel to catch exceptions at the
generator boundary:

```python
def _wrap_kernel_as_result(execution_kernel):
    def wrapped_kernel(*args, **kwargs):
        try:
            gen_or_value = execution_kernel(*args, **kwargs)
        except Exception as exc:
            return Err(exc)

        if not inspect.isgenerator(gen_or_value):
            return Ok(gen_or_value)

        gen = gen_or_value
        # ... forward yields, catch StopIteration → Ok, Exception → Err
    return wrapped_kernel
```

No handler is needed — `Safe` operates purely at the program level by
intercepting the generator protocol.

## Usage Patterns

### With Gather (partial results)

Without `Safe`, `Gather` fails on the first error (fail-fast):

```python
@do
def fragile():
    t1 = yield Spawn(may_fail_1())
    t2 = yield Spawn(may_fail_2())
    results = yield Gather(t1, t2)  # raises on first failure
```

With `Safe`, collect all results:

```python
@do
def resilient():
    t1 = yield Spawn(Safe(may_fail_1()))
    t2 = yield Spawn(Safe(may_fail_2()))
    results = yield Gather(t1, t2)
    # results: [Ok(value1), Err(error2)]
    for r in results:
        match r:
            case Ok(v): print(f"success: {v}")
            case Err(e): print(f"failure: {e}")
```

### With Race (error-tolerant)

```python
@do
def race_safe():
    t1 = yield Spawn(Safe(fast_but_flaky()))
    t2 = yield Spawn(Safe(slow_but_reliable()))
    result = yield Race(t1, t2)
    # result.value is Ok(...) or Err(...)
```

### Standalone error capture

```python
@do
def try_something():
    result = yield Safe(risky_operation())
    match result:
        case Ok(v): return v
        case Err(e): return default_value
```

### Nested Safe

`Safe(Safe(program))` is valid. The outer `Safe` catches any error from the
inner, which itself always returns `Ok` or `Err`. So the outer always
returns `Ok(Ok(value))` or `Ok(Err(error))` — never `Err(...)`.

## Properties

- **Transparent to effects**: All effects from the sub-program pass through
  normally. Only the final return/raise is wrapped.
- **No handler required**: Works with any handler stack.
- **Composable**: Can be nested, combined with Spawn/Gather/Race.
- **Idempotent on success**: `Safe(Safe(program))` on a successful program
  returns `Ok(Ok(value))`.

## Related Specs

| Spec | Relationship |
|------|-------------|
| SPEC-SCHED-001 | Gather is fail-fast; `Safe` is the escape hatch for partial results |
| SPEC-EFF-004 | Control effects (Local, Intercept) compose with Safe |
| SPEC-EFF-013 | Ok/Err types returned by Safe. Same types used by TaskCompleted and RunResult. |

## Location

`doeff/effects/result.py` — exports `Safe`, `safe`, `ResultSafeEffect`
