# Error Handling

This chapter covers doeff's comprehensive error handling system using the Result monad and error effects.

## Table of Contents

- [Result Type](#result-type)
- [Fail Effect](#fail-effect)
- [Safe Effect](#safe-effect)
- [Retry Effect](#retry-effect)
- [Finally Effect](#finally-effect)
- [FirstSuccess Effect](#firstsuccess-effect)
- [Error Patterns](#error-patterns)
- [Migration from Catch/Recover](#migration-from-catchrecover)

## Result Type

doeff uses the `Result[T]` type to represent success or failure:

```python
from doeff import Ok, Err, Result

# Success
success: Result[int] = Ok(42)
assert success.is_ok()  # Note: is_ok() is a method
assert success.value == 42

# Failure
failure: Result[int] = Err(Exception("error"))
assert failure.is_err()  # Note: is_err() is a method
assert isinstance(failure.error, Exception)
```

### Pattern Matching

```python
runtime = AsyncioRuntime()
result = await runtime.run(my_program())

match result.result:
    case Ok(value):
        print(f"Success: {value}")
    case Err(error):
        print(f"Error: {error}")
```

### Result in RuntimeResult

```python
@do
def my_program():
    yield Log("Processing...")
    return 42

runtime = AsyncioRuntime()
result = await runtime.run(my_program())

# Check success
if result.is_ok:
    print(f"Value: {result.value}")
else:
    print(f"Error: {result.error}")
```

## Fail Effect

`Fail(exception)` immediately fails the program with an exception.

### Basic Failure

```python
@do
def failing_program():
    yield Log("About to fail...")
    yield Fail(Exception("Something went wrong"))
    yield Log("This never executes")
    return "never returned"

runtime = AsyncioRuntime()
result = await runtime.run(failing_program())
assert result.is_err
assert str(result.error) == "Something went wrong"
```

### Conditional Failure

```python
@do
def validate_input(value):
    if value < 0:
        yield Fail(ValueError("Value must be non-negative"))
    
    if value > 100:
        yield Fail(ValueError("Value must be <= 100"))
    
    yield Log(f"Valid value: {value}")
    return value

# This fails
runtime = AsyncioRuntime()
result = await runtime.run(validate_input(-5))
assert result.is_err
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
        yield Fail(ValidationError("email", "Email is required"))
    
    if "@" not in user_data["email"]:
        yield Fail(ValidationError("email", "Invalid email format"))
    
    return user_data
```

## Safe Effect

`Safe(sub_program)` wraps execution and returns a `Result` type, allowing you to handle errors explicitly.

### Basic Safe Usage

```python
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
        return result.value
    else:
        yield Log(f"Fetch failed: {result.error}")
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
        yield Log(f"Operation failed: {result.error}")
        yield Fail(RuntimeError(f"Wrapped error: {result.error}"))
    
    return result.value
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
    successes = [r.value for r in results if r.is_ok()]
    failures = [r.error for r in results if r.is_err()]
    
    yield Log(f"Successes: {len(successes)}, Failures: {len(failures)}")
    
    return successes
```

### Safe with Parallel

```python
@do
def parallel_safe_operations():
    # Run multiple operations, some might fail
    tasks = [process_item(i) for i in range(10)]
    
    # Wrap each in Safe to get Results
    safe_tasks = [Safe(task) for task in tasks]
    
    # Run all in parallel
    results = yield Parallel(*[Await(t) for t in safe_tasks])
    
    # Process results
    successes = [r.value for r in results if r.is_ok()]
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
        return result.value
    
    yield Log(f"Primary failed: {result.error}, trying backup...")
    
    # Try backup source
    backup_result = yield Safe(fetch_from_backup())
    
    if backup_result.is_ok():
        return backup_result.value
    
    # Both failed, use default
    yield Log("All sources failed, using default")
    return get_default_data()
```

### Safe with Logging

```python
@do
def logged_safe_operation(operation_name):
    yield Log(f"Starting: {operation_name}")
    
    result = yield Safe(risky_operation())
    
    if result.is_ok():
        yield Log(f"{operation_name} succeeded with: {result.value}")
        return result.value
    else:
        yield Log(f"{operation_name} failed with: {result.error}")
        return None
```

## Retry Effect

`Retry(sub_program, max_attempts, delay_ms=0, delay_strategy=None)` retries on failure.

### Basic Retry

```python
@do
def unstable_operation():
    import random
    if random.random() < 0.7:
        yield Fail(Exception("Random failure"))
    yield Log("Operation succeeded!")
    return "success"

@do
def with_retry():
    result = yield Retry(
        unstable_operation(),
        max_attempts=5,
        delay_ms=100
    )
    return result

# Will retry up to 5 times with 100ms delay
runtime = AsyncioRuntime()
result = await runtime.run(with_retry())
```

### Randomized Backoff with delay_strategy

```python
import random

def jittered_delay(attempt: int, _error: Exception | None) -> float:
    upper = min(30.0, 2 ** (attempt - 1))
    return random.uniform(1.0, max(1.0, upper))

@do
def fetch_with_retry():
    return (yield Retry(
        unstable_operation(),
        max_attempts=5,
        delay_strategy=jittered_delay,
    ))
```

### Exponential Backoff (Manual)

```python
@do
def retry_with_backoff():
    attempts = 0
    delay = 100  # Start with 100ms
    
    while attempts < 5:
        result = yield Safe(fetch_api_data())
        
        if result.is_ok():
            return result.value
        
        attempts += 1
        if attempts < 5:
            yield Log(f"Attempt {attempts} failed, waiting {delay}ms...")
            yield Await(asyncio.sleep(delay / 1000))
            delay *= 2  # Double the delay
    
    yield Fail(Exception("Max retries exceeded"))
```

### Retry with Logging

```python
@do
def logged_retry():
    @do
    def attempt():
        attempt_num = yield Get("attempt")
        yield Modify("attempt", lambda x: x + 1)
        yield Log(f"Attempt #{attempt_num + 1}")
        
        # Simulated operation
        success = yield unstable_operation()
        return success
    
    yield Put("attempt", 0)
    result = yield Retry(attempt(), max_attempts=3)
    
    final_attempt = yield Get("attempt")
    yield Log(f"Succeeded after {final_attempt} attempts")
    
    return result
```

## Finally Effect

`Finally(sub_program, finalizer)` ensures cleanup code runs.

### Resource Cleanup

```python
@do
def with_cleanup():
    @do
    def acquire_and_use():
        yield Put("resource_acquired", True)
        yield Log("Using resource...")
        # Might fail here
        result = yield risky_operation()
        return result
    
    @do
    def cleanup():
        yield Log("Cleaning up resource...")
        yield Put("resource_acquired", False)
    
    # Cleanup always runs, even on failure
    result = yield Finally(
        acquire_and_use(),
        cleanup()
    )
    
    return result
```

### File Handle Pattern

```python
@do
def process_file(filename):
    @do
    def read_and_process():
        yield Log(f"Opening {filename}")
        content = yield Await(read_file(filename))
        result = yield process_content(content)
        return result
    
    @do
    def close_file():
        yield Log(f"Closing {filename}")
        # Cleanup code here
    
    result = yield Finally(
        read_and_process(),
        close_file()
    )
    
    return result
```

### Nested Finally

```python
@do
def nested_cleanup():
    @do
    def with_db():
        result = yield Finally(
            db_operation(),
            cleanup_db()
        )
        return result
    
    @do
    def with_cache():
        result = yield Finally(
            with_db(),
            cleanup_cache()
        )
        return result
    
    # Both cleanups run in reverse order
    return (yield with_cache())
```

## FirstSuccess Effect

`FirstSuccess(*programs)` tries programs until one succeeds.

### Fallback Chain

```python
@do
def multi_source_fetch():
    result = yield FirstSuccess(
        fetch_from_cache(),
        fetch_from_primary_db(),
        fetch_from_backup_db(),
        fetch_from_default()
    )
    return result

# Tries each in order, returns first success
```

### Service Discovery Pattern

```python
@do
def fetch_from_any_server():
    servers = [
        "https://server1.example.com",
        "https://server2.example.com",
        "https://server3.example.com"
    ]
    
    # Try each server
    result = yield FirstSuccess(*[
        fetch_from_server(url) for url in servers
    ])
    
    return result

@do
def fetch_from_server(url):
    yield Log(f"Trying {url}")
    response = yield Await(httpx.get(url))
    if response.status_code != 200:
        yield Fail(Exception(f"HTTP {response.status_code}"))
    return response.json()
```

## Error Patterns

### Validate-Process-Handle Pattern

```python
@do
def validate_process_handle(data):
    # Validation
    if not data:
        yield Fail(ValueError("Data cannot be empty"))
    
    # Safe processing
    result = yield Safe(process_data(data))
    
    # Handle result
    match result:
        case Ok(value):
            yield Log(f"Processed successfully: {value}")
            return value
        case Err(error):
            yield Log(f"Processing failed: {error}")
            # Fallback
            return default_value()
```

### Retry with Exponential Backoff

```python
@do
def robust_api_call(url):
    max_attempts = 5
    base_delay = 100
    
    for attempt in range(max_attempts):
        result = yield Safe(Await(httpx.get(url)))
        
        if result.is_ok():
            return result.value
        
        if attempt < max_attempts - 1:
            delay = base_delay * (2 ** attempt)
            yield Log(f"Attempt {attempt + 1} failed, retrying in {delay}ms...")
            yield Await(asyncio.sleep(delay / 1000))
    
    yield Fail(Exception(f"Failed after {max_attempts} attempts"))
```

### Circuit Breaker Pattern

```python
@do
def with_circuit_breaker(service_name):
    failures = yield Get(f"{service_name}_failures")
    
    if failures >= 5:
        yield Log(f"Circuit breaker OPEN for {service_name}")
        yield Fail(Exception("Circuit breaker open"))
    
    result = yield Safe(call_service(service_name))
    
    if result.is_err():
        yield Modify(f"{service_name}_failures", lambda x: x + 1)
        yield Fail(result.error)
    else:
        yield Put(f"{service_name}_failures", 0)
        return result.value
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
            results.append(result.value)
        else:
            errors.append({"item": item, "error": str(result.error)})
    
    yield Log(f"Processed {len(results)}/{len(items)} items")
    
    if errors:
        yield Log(f"Errors: {errors}")
    
    return {"successes": results, "errors": errors}
```

## Best Practices

### When to Use Each Effect

**Fail:** Explicit error conditions
```python
if invalid_input:
    yield Fail(ValueError("Invalid input"))
```

**Safe:** When you need to handle errors and continue
```python
result = yield Safe(risky_operation())
if result.is_ok():
    return result.value
else:
    return fallback_value
```

**Retry:** Transient failures
```python
result = yield Retry(network_call(), max_attempts=3)
```

**Finally:** Resource cleanup
```python
yield Finally(use_resource(), cleanup_resource())
```

**FirstSuccess:** Multiple fallback options
```python
data = yield FirstSuccess(cache(), db(), api())
```

### Error Context

Always provide context in errors:

```python
@do
def with_context():
    try:
        user_id = yield Get("user_id")
        data = yield fetch_user_data(user_id)
        return data
    except KeyError:
        yield Fail(ValueError(f"Missing user_id in state"))
```

## Migration from Catch/Recover

If you're migrating from older doeff versions that used `Catch` and `Recover`, here's how to update your code:

### Catch to Safe

**Before (deprecated):**
```python
result = yield Catch(
    risky_operation(),
    lambda e: "fallback"
)
```

**After:**
```python
safe_result = yield Safe(risky_operation())
result = safe_result.value if safe_result.is_ok() else "fallback"
```

### Catch with Error Transformation

**Before (deprecated):**
```python
result = yield Catch(
    risky_operation(),
    lambda e: handle_error(e)
)
```

**After:**
```python
safe_result = yield Safe(risky_operation())
if safe_result.is_ok():
    result = safe_result.value
else:
    result = handle_error(safe_result.error)
```

### Recover to Safe

**Before (deprecated):**
```python
data = yield Recover(
    fetch_data(),
    fallback=[]
)
```

**After:**
```python
safe_result = yield Safe(fetch_data())
data = safe_result.value if safe_result.is_ok() else []
```

### Recover with Fallback Program

**Before (deprecated):**
```python
data = yield Recover(
    fetch_from_primary(),
    fallback=fetch_from_backup()
)
```

**After:**
```python
safe_result = yield Safe(fetch_from_primary())
if safe_result.is_ok():
    data = safe_result.value
else:
    data = yield fetch_from_backup()
```

Or use `FirstSuccess`:
```python
data = yield FirstSuccess(
    fetch_from_primary(),
    fetch_from_backup()
)
```

## Summary

| Effect | Purpose | When to Use |
|--------|---------|-------------|
| `Fail(exc)` | Raise error | Validation, explicit failures |
| `Safe(prog)` | Get Result type | Need Ok/Err inspection, error recovery |
| `Retry(prog, n)` | Retry on failure | Transient errors, network calls |
| `Finally(prog, cleanup)` | Always cleanup | Resource management |
| `FirstSuccess(*progs)` | Try alternatives | Multiple fallback sources |

## Next Steps

- **[IO Effects](06-io-effects.md)** - Side effects and IO operations
- **[Patterns](12-patterns.md)** - Advanced error handling patterns
- **[Basic Effects](03-basic-effects.md)** - Combine with State, Reader, Writer
