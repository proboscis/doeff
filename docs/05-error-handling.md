# Error Handling

This chapter covers doeff's error handling approach using the Result monad and the Safe effect combined with native Python patterns.

## Table of Contents

- [Result Type](#result-type)
- [Safe Effect](#safe-effect)
- [Native Python Patterns](#native-python-patterns)
- [Error Patterns](#error-patterns)
- [Migration from Dropped Effects](#migration-from-dropped-effects)

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
result = await runtime.run_safe(my_program())

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
result = await runtime.run_safe(my_program())

# Check success
if result.is_ok:
    print(f"Value: {result.value}")
else:
    print(f"Error: {result.error}")
```

## Safe Effect

`Safe(sub_program)` wraps execution and returns a `Result` type, allowing you to handle errors explicitly without stopping program execution.

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
        raise RuntimeError(f"Wrapped error: {result.error}")
    
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

### Safe with Gather

```python
@do
def parallel_safe_operations():
    # Run multiple operations, some might fail
    tasks = [process_item(i) for i in range(10)]
    
    # Wrap each in Safe to get Results
    safe_tasks = [Safe(task) for task in tasks]
    
    # Run all in parallel using Gather
    results = yield Gather(safe_tasks)
    
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
import asyncio

@do
def retry_with_backoff(operation, max_attempts=3, base_delay=0.1):
    """Retry an operation with exponential backoff."""
    last_error = None
    
    for attempt in range(max_attempts):
        result = yield Safe(operation())
        
        if result.is_ok():
            return result.value
        
        last_error = result.error
        if attempt < max_attempts - 1:
            delay = base_delay * (2 ** attempt)
            yield Log(f"Attempt {attempt + 1} failed, retrying in {delay}s...")
            yield Await(asyncio.sleep(delay))
    
    raise Exception(f"Failed after {max_attempts} attempts: {last_error}")
```

### Cleanup Pattern with Native Python

Use try/finally for resource cleanup:

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

### Multiple Fallbacks Pattern

Try multiple sources sequentially:

```python
@do
def fetch_with_fallbacks():
    sources = [
        ("cache", fetch_from_cache),
        ("primary_db", fetch_from_primary_db),
        ("backup_db", fetch_from_backup_db),
    ]
    
    for name, fetch_fn in sources:
        result = yield Safe(fetch_fn())
        if result.is_ok():
            yield Log(f"Fetched from {name}")
            return result.value
        yield Log(f"{name} failed: {result.error}")
    
    # All sources failed
    yield Log("All sources failed, using default")
    return get_default_data()
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
            # Fallback
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
        raise result.error
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
    return result.value
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
result = safe_result.value if safe_result.is_ok() else "fallback"
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
data = safe_result.value if safe_result.is_ok() else []
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
import asyncio

for attempt in range(5):
    result = yield Safe(unstable_operation())
    if result.is_ok():
        break
    if attempt < 4:
        yield Await(asyncio.sleep(0.1))
else:
    raise Exception("Max retries exceeded")

final_result = result.value
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

final_result = result.value
```

## Summary

| Approach | Purpose | When to Use |
|----------|---------|-------------|
| `raise` | Signal error | Validation, explicit failures |
| `Safe(prog)` | Get Result type | Need Ok/Err inspection, error recovery |
| `try/finally` | Ensure cleanup | Resource management |
| Manual loop + Safe | Retry logic | Transient errors, network calls |

**Key Principles:**
- Use native Python `raise` for signaling errors
- Use `Safe` effect to catch errors and continue execution
- Use `try/finally` for cleanup logic
- Implement retry and fallback patterns with loops and `Safe`

## Next Steps

- **[IO Effects](06-io-effects.md)** - Side effects and IO operations
- **[Patterns](12-patterns.md)** - Advanced error handling patterns
- **[Basic Effects](03-basic-effects.md)** - Combine with State, Reader, Writer
