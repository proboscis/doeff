# Error Handling

This chapter covers doeff's error handling approach using the Result type, the Safe program
combinator, RuntimeResult protocol, and native Python patterns.

## Table of Contents

- [RuntimeResult Protocol](#runtimeresult-protocol)
- [Result Type](#result-type)
- [Safe Program Combinator](#safe-program-combinator)
- [Native Python Patterns](#native-python-patterns)
- [Error Patterns](#error-patterns)
- [Stack Traces and Debugging](#stack-traces-and-debugging)
- [Migration from Dropped Effects](#migration-from-dropped-effects)

## RuntimeResult Protocol

`run()` and `arun()` return a `RuntimeResult[T]`. This provides both the computation outcome and full debugging context.

### Basic Usage

```python
from doeff import do, Log, Ask, run, default_handlers

@do
def my_program():
    yield Log("Processing...")
    config = yield Ask("config")
    return config["value"]

def main():
    result = run(
        my_program(),
        default_handlers(),
        env={"config": {"value": 42}}
    )

    # Check success with methods (not properties!)
    if result.is_ok():
        print(f"Value: {result.value}")
    else:
        print(f"Error: {result.error}")

main()
```

### RuntimeResult API

```python
# Core properties
result.result      # Result[T]: Ok(value) or Err(error)
result.value       # T: Unwrap Ok value (raises if Err)
result.error       # BaseException: Get error (raises if Ok)
result.raw_store   # dict: Final store state

# Methods - NOT properties!
result.is_ok()     # bool: True if success
result.is_err()    # bool: True if failure

# Access logs and graph from raw_store
logs = result.raw_store.get("__log__", [])
graph = result.raw_store.get("__graph__")

# Stack traces (for debugging)
result.k_stack        # KStackTrace: Continuation stack
result.effect_stack   # EffectStackTrace: Effect call tree
result.python_stack   # PythonStackTrace: Python source locations

# Display
result.format()              # str: Condensed format
result.format(verbose=True)  # str: Full debugging output
```

### Checking Results

**IMPORTANT:** `is_ok()` and `is_err()` are **methods**, not properties!

```python
# CORRECT
if result.is_ok():
    print(result.value)

# WRONG - will always be truthy because it's a method reference
if result.is_ok:   # This is WRONG - always True!
    print(result.value)
```

### Pattern Matching

```python
from doeff import Ok, Err, run, default_handlers

def main():
    result = run(my_program(), default_handlers())

    match result.result:
        case Ok(value):
            print(f"Success: {value}")
        case Err(error):
            print(f"Error: {error}")
```

## Result Type

doeff uses the `Result[T]` type internally to represent success or failure:

```python
from doeff import Ok, Err, Result

# Success
success: Result[int] = Ok(42)
assert success.is_ok()
print(success.ok())  # 42

# Failure
failure: Result[int] = Err(Exception("error"))
assert failure.is_err()
print(failure.err())  # Exception("error")
```

## Safe Program Combinator

`Safe(sub_program)` is a Python-level program combinator. It wraps a sub-program and returns a
`Result` value:

- Success: `Ok(value)`
- Failure: `Err(exception)`

Unlike handler-dispatched algebraic effects, `Safe` does not require a dedicated handler. It
works by wrapping the sub-program execution kernel at the generator boundary.

### How Safe Works Internally

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
        # forward yields as-is
        # StopIteration(value) -> Ok(value)
        # Exception(exc) -> Err(exc)
        ...

    return wrapped_kernel
```

This wrapper is transparent to inner effects. The inner program still yields `Ask`, `Put`,
`Local`, `Intercept`, and other effects normally. `Safe` only changes the final success/failure
surface to `Ok`/`Err`.

### Basic Safe Usage

```python
from doeff import do, Safe, Log, Ok, Err

@do
def risky_operation():
    raise ValueError("Something went wrong!")

@do
def safe_operation():
    result = yield Safe(risky_operation())

    match result:
        case Ok(value):
            yield Log(f"Success: {value}")
            return value
        case Err(error):
            yield Log(f"Error: {error}")
            return "default"
```

### Using is_ok / is_err

```python
@do
def with_fallback():
    result = yield Safe(fetch_data())

    if result.is_ok():
        return result.ok()
    else:
        yield Log(f"Fetch failed: {result.err()}")
        return []  # Fallback value
```

### Safe with Error Transformation

Transform errors while preserving the success path:

```python
@do
def transform_errors():
    result = yield Safe(risky_operation())

    if result.is_err():
        # Log and transform the error
        yield Log(f"Operation failed: {result.err()}")
        raise RuntimeError(f"Wrapped error: {result.err()}")

    return result.ok()
```

### Multiple Safe Operations

```python
@do
def multiple_safe_operations():
    results = []

    # Try multiple operations, collect results
    for i in range(5):
        result = yield Safe(process_item(i))
        results.append(result)

    # Count successes and failures
    successes = [r.ok() for r in results if r.is_ok()]
    failures = [r.err() for r in results if r.is_err()]

    yield Log(f"Successes: {len(successes)}, Failures: {len(failures)}")

    return successes
```

### Safe with Gather

```python
from doeff import do, Safe, Gather, Log

@do
def parallel_safe_operations():
    # Run multiple operations, some might fail
    tasks = [process_item(i) for i in range(10)]

    # Wrap each in Safe to get Results
    safe_tasks = [Safe(task) for task in tasks]

    # Run all in parallel using Gather
    results = yield Gather(*safe_tasks)

    # Process results
    successes = [r.ok() for r in results if r.is_ok()]
    yield Log(f"Completed {len(successes)}/10 tasks")

    return successes
```

### Safe for Conditional Recovery

```python
@do
def fetch_with_fallback():
    # Try primary source
    result = yield Safe(fetch_from_primary())

    if result.is_ok():
        return result.ok()

    yield Log(f"Primary failed: {result.err()}, trying backup...")

    # Try backup source
    backup_result = yield Safe(fetch_from_backup())

    if backup_result.is_ok():
        return backup_result.ok()

    # Both failed, use default
    yield Log("All sources failed, using default")
    return get_default_data()
```

### Composition Rules

#### Safe + Put (No Rollback)

Per [SPEC-EFF-004](../specs/effects/SPEC-EFF-004-control.md), `Safe` does **NOT** roll back state
changes when an error occurs:

```python
@do
def demo_no_rollback():
    yield Put("counter", 0)

    result = yield Safe(failing_with_side_effects())

    # Even though the operation failed, counter is 10
    counter = yield Get("counter")
    yield Log(f"Counter after failure: {counter}")  # 10, not 0!

    return result

@do
def failing_with_side_effects():
    yield Modify("counter", lambda x: x + 10)  # This persists!
    raise ValueError("Oops!")
```

#### Safe + Local (Environment Restored)

Environment changes from `Local` are restored after `Safe` completes, including caught errors:

```python
@do
def failing_in_local():
    _ = yield Ask("key")  # "modified"
    raise ValueError("boom")

@do
def safe_local_example():
    before = yield Ask("key")  # "original"
    result = yield Safe(Local({"key": "modified"}, failing_in_local()))
    after = yield Ask("key")  # "original" (restored)
    return (before, result.is_err(), after)
```

#### Nested Safe (Inner Catches First)

Nested `Safe` catches from the inside out:

```python
@do
def failing_program():
    raise ValueError("inner error")

@do
def nested_safe_example():
    result = yield Safe(Safe(failing_program()))
    # result is Ok(Err(ValueError("inner error")))
    return result
```

The inner `Safe` converts the exception to `Err(...)`. The outer `Safe` sees a normal return
value and wraps it in `Ok(...)`.

#### Safe + Intercept

`Safe` and `Intercept` compose directly:

```python
@do
def failing_program():
    _ = yield Ask("key")
    raise ValueError("caught error")

def passthrough(effect):
    return None

@do
def safe_intercept_example():
    result = yield Safe(Intercept(failing_program(), passthrough))
    return result
```

`Intercept` still sees and can transform effects inside `failing_program`. If execution still
raises, `Safe` converts that failure to `Err(exception)` instead of terminating the parent
program.

## Native Python Patterns

doeff embraces native Python for error handling. Use `raise` to signal errors and try/except for handling them.

### Explicit Errors with raise

```python
@do
def validate_input(value):
    if value < 0:
        raise ValueError("Value must be non-negative")

    if value > 100:
        raise ValueError("Value must be <= 100")

    yield Log(f"Valid value: {value}")
    return value
```

### Custom Exceptions

```python
class ValidationError(Exception):
    def __init__(self, field, message):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")

@do
def validate_user(user_data):
    if "email" not in user_data:
        raise ValidationError("email", "Email is required")

    if "@" not in user_data["email"]:
        raise ValidationError("email", "Invalid email format")

    return user_data
```

### Retry Pattern with Native Python

Implement retry logic using a simple loop:

```python
from doeff import do, Safe, Await, Log, Delay

@do
def retry_with_backoff(max_attempts=3, base_delay=0.1):
    """Retry an operation with exponential backoff."""
    last_error = None

    for attempt in range(max_attempts):
        result = yield Safe(operation())

        if result.is_ok():
            return result.ok()

        last_error = result.err()
        if attempt < max_attempts - 1:
            delay = base_delay * (2 ** attempt)
            yield Log(f"Attempt {attempt + 1} failed, retrying in {delay}s...")
            yield Delay(delay)

    raise Exception(f"Failed after {max_attempts} attempts: {last_error}")
```

### Cleanup Pattern with try/finally

```python
@do
def with_resource_cleanup():
    yield Log("Acquiring resource...")
    yield Put("resource_acquired", True)

    try:
        result = yield risky_operation()
        return result
    finally:
        yield Log("Cleaning up resource...")
        yield Put("resource_acquired", False)
```

## Error Patterns

### Validate-Process-Handle Pattern

```python
@do
def validate_process_handle(data):
    # Validation with native raise
    if not data:
        raise ValueError("Data cannot be empty")

    # Safe processing
    result = yield Safe(process_data(data))

    # Handle result
    match result:
        case Ok(value):
            yield Log(f"Processed successfully: {value}")
            return value
        case Err(error):
            yield Log(f"Processing failed: {error}")
            return default_value()
```

### Circuit Breaker Pattern

```python
@do
def with_circuit_breaker(service_name):
    failures = yield Get(f"{service_name}_failures")

    if failures >= 5:
        yield Log(f"Circuit breaker OPEN for {service_name}")
        raise Exception("Circuit breaker open")

    result = yield Safe(call_service(service_name))

    if result.is_err():
        yield Modify(f"{service_name}_failures", lambda x: x + 1)
        raise result.err()
    else:
        yield Put(f"{service_name}_failures", 0)
        return result.ok()
```

### Error Aggregation

```python
@do
def process_batch_with_errors(items):
    results = []
    errors = []

    for item in items:
        result = yield Safe(process_item(item))

        if result.is_ok():
            results.append(result.ok())
        else:
            errors.append({"item": item, "error": str(result.err())})

    yield Log(f"Processed {len(results)}/{len(items)} items")

    if errors:
        yield Log(f"Errors: {errors}")

    return {"successes": results, "errors": errors}
```

## Stack Traces and Debugging

When errors occur, `RuntimeResult` provides three complementary stack traces for debugging.

### Three Stack Trace Views

1. **k_stack (Continuation Stack)**: Shows active control-flow frames (SafeFrame, LocalFrame, etc.)
2. **effect_stack (Effect Call Tree)**: Shows which `@do` functions called which effects
3. **python_stack (Python Traceback)**: Standard Python source locations

### Accessing Stack Traces

```python
from doeff import run, default_handlers

def main():
    result = run(failing_program(), default_handlers())

    if result.is_err():
        # Full formatted output
        print(result.format(verbose=True))

        # Individual stacks
        print(result.k_stack.format())
        print(result.effect_stack.format())
        print(result.python_stack.format())

        # Quick effect path
        print(f"Effect path: {result.effect_stack.get_effect_path()}")
```

### Example Output (Verbose)

```
===============================================================================
                              RUNTIME RESULT
===============================================================================

Status: Err(KeyError: 'missing_config')

-------------------------------------------------------------------------------
                               ROOT CAUSE
-------------------------------------------------------------------------------
KeyError: 'missing_config'

-------------------------------------------------------------------------------
                             PYTHON STACK
-------------------------------------------------------------------------------
Python Stack:
  File "app.py", line 42, in main
    config = yield load_settings()
  File "settings.py", line 15, in load_settings
    value = yield Ask('missing_config')

-------------------------------------------------------------------------------
                           EFFECT CALL TREE
-------------------------------------------------------------------------------
Effect Call Tree:
  └─ main()
     └─ load_settings()
        ├─ Ask('app_name')
        └─ Ask('missing_config')  <-- ERROR

-------------------------------------------------------------------------------
                         CONTINUATION STACK (K)
-------------------------------------------------------------------------------
Continuation Stack (K):
  [0] SafeFrame            - will catch this error
  [1] LocalFrame           - env={'debug': True}

-------------------------------------------------------------------------------
                              STATE & LOG
-------------------------------------------------------------------------------
State:
  initialized: True
  step: 3

Log:
  [0] "Starting application"
  [1] "Loading settings..."

===============================================================================
```

### Example Output (Condensed)

```python
print(result.format())
```

```
Err(KeyError: 'missing_config')

Root Cause: KeyError: 'missing_config'

  File "settings.py", line 15, in load_settings
    value = yield Ask('missing_config')

Effect path: main() -> load_settings() -> Ask('missing_config')

K: [SafeFrame, LocalFrame]
```

## Best Practices

### When to Use Safe vs raise

**Use `raise` for:**
- Validation failures
- Explicit error conditions that should stop execution
- Unrecoverable errors

```python
if invalid_input:
    raise ValueError("Invalid input")
```

**Use `Safe` for:**
- Operations that might fail but you want to continue
- When you need to inspect the error and decide what to do
- Collecting results from multiple operations

```python
result = yield Safe(risky_operation())
if result.is_ok():
    return result.ok()
else:
    return fallback_value
```

### Error Context

Always provide context in errors:

```python
@do
def with_context():
    user_id = yield Get("user_id")
    if user_id is None:
        raise ValueError("Missing user_id in state")

    data = yield fetch_user_data(user_id)
    return data
```

## Migration from Dropped Effects

If you're migrating from older doeff versions that used `Fail`, `Catch`, `Recover`, `Retry`, `Finally`, or `FirstSuccess`, here's how to update your code.

### Fail to raise

**Before (dropped):**
```python
yield Fail(ValueError("error"))
```

**After:**
```python
raise ValueError("error")
```

### Catch to Safe

**Before (dropped):**
```python
result = yield Catch(
    risky_operation(),
    lambda e: "fallback"
)
```

**After:**
```python
safe_result = yield Safe(risky_operation())
result = safe_result.ok() if safe_result.is_ok() else "fallback"
```

### Recover to Safe

**Before (dropped):**
```python
data = yield Recover(
    fetch_data(),
    fallback=[]
)
```

**After:**
```python
safe_result = yield Safe(fetch_data())
data = safe_result.ok() if safe_result.is_ok() else []
```

### Retry to Manual Loop

**Before (dropped):**
```python
result = yield Retry(
    unstable_operation(),
    max_attempts=5,
    delay_ms=100
)
```

**After:**
```python
for attempt in range(5):
    result = yield Safe(unstable_operation())
    if result.is_ok():
        break
    if attempt < 4:
        yield Delay(0.1)
else:
    raise Exception("Max retries exceeded")

final_result = result.ok()
```

### Finally to try/finally

**Before (dropped):**
```python
result = yield Finally(
    use_resource(),
    cleanup_resource()
)
```

**After:**
```python
try:
    result = yield use_resource()
finally:
    yield cleanup_resource()
```

### FirstSuccess to Sequential Safe

**Before (dropped):**
```python
result = yield FirstSuccess(
    fetch_from_cache(),
    fetch_from_db(),
    fetch_from_api()
)
```

**After:**
```python
for fetch_fn in [fetch_from_cache, fetch_from_db, fetch_from_api]:
    result = yield Safe(fetch_fn())
    if result.is_ok():
        break
else:
    raise Exception("All sources failed")

final_result = result.ok()
```

## Summary

| Approach | Purpose | When to Use |
|----------|---------|-------------|
| `RuntimeResult` | Get full execution context | Always returned from `run()`/`arun()` |
| `raise` | Signal error | Validation, explicit failures |
| `Safe(prog)` | Get Result type | Need Ok/Err inspection, error recovery |
| `try/finally` | Ensure cleanup | Resource management |
| Manual loop + Safe | Retry logic | Transient errors, network calls |

**Key Principles:**
- `run()` and `arun()` return `RuntimeResult` (not raw values)
- Use `is_ok()` / `is_err()` as **methods** (with parentheses!)
- Use `result.value` to get the unwrapped value (raises on error)
- Use `result.error` to get the exception (raises on success)
- Use `result.raw_store` to access final store state
- Access logs via `result.raw_store.get("__log__", [])`
- Use `Safe` program combinator to catch errors and continue execution
- Use native Python `raise` for signaling errors
- Use `try/finally` for cleanup logic
- Access stack traces via `k_stack`, `effect_stack`, `python_stack`

## Next Steps

- **[IO Effects](06-io-effects.md)** - Side effects and IO operations
- **[Effects Matrix](21-effects-matrix.md)** - Complete effect reference
- **[Patterns](12-patterns.md)** - Advanced error handling patterns
- **[Basic Effects](03-basic-effects.md)** - Combine with State, Reader, Writer
