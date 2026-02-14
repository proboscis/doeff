# DOEFF014: Consider Effect-Based Error Handling

## Summary

Detects try-except blocks and suggests considering doeff's effect-based error handling (`Safe`, `Catch`, `Recover`, etc.) for complex cases. Native try-except now works in @do functions.

## Rationale

**Native try-except now works in @do functions!** You can use Python's standard try-except syntax to catch errors from yielded effects and sub-programs.

However, for complex error handling scenarios, effect-based handlers offer advantages:

1. **Composability** - Effect handlers can be combined and chained
2. **Explicit error flow** - Result types make success/failure explicit in the type system
3. **Integration** - Effect handlers integrate with logging, tracing, and other effects

Both approaches work. Choose based on your needs:
- **Simple cases**: Use native try-except
- **Complex cases**: Consider effect-based handlers

## Examples

### Native try-except (now works!)

```python
from doeff import do, EffectGenerator, Fail

@do
def program() -> EffectGenerator[str]:
    try:
        value = yield risky_operation()
        return f"success: {value}"
    except ValueError as e:
        return f"caught: {e}"  # This works!

@do
def nested_handling() -> EffectGenerator[str]:
    try:
        try:
            yield inner_operation()
        except ValueError:
            yield log("ValueError caught")
    except RuntimeError:
        yield log("RuntimeError caught")
    return "done"

@do
def with_finally() -> EffectGenerator[str]:
    try:
        result = yield acquire_resource()
        return result
    except Exception as e:
        return f"error: {e}"
    finally:
        cleanup()  # Executes on success or failure
```

### Effect-based alternatives (for complex cases)

```python
from doeff import do, Safe, Ok, Err, Catch, Recover

# Pattern 1: Use Safe to get Result object
@do
def safe_operation():
    result = yield Safe(risky_operation())

    match result:
        case Ok(value):
            return value
        case Err(error):
            return default_value()

# Pattern 2: Use Recover for fallback values
@do
def with_recover():
    # Returns fallback value if fetch_data fails
    data = yield Recover(fetch_data(), fallback=[])
    return data

# Pattern 3: Use Catch for error transformation
@do
def with_catch():
    result = yield Catch(
        fetch_data(),
        lambda e: handle_error(e)
    )
    return result

# Pattern 4: Combine both approaches
@do
def mixed_handling():
    # Effect-based for complex recovery
    config = yield Safe(load_config())

    # Native try-except for simple cases
    try:
        data = yield fetch_data(config.value)
    except NetworkError:
        data = cached_data

    return data
```

## When to Use Each Approach

| Scenario | Recommended Approach |
|----------|---------------------|
| Simple error recovery | Native try-except |
| Fallback values | `Recover(program, fallback)` |
| Error transformation | `Catch(program, handler)` |
| Result type needed | `Safe(program)` |
| Multiple alternatives | `program.first_success(alt1, alt2)` |
| Retry logic | `Retry(program, max_attempts)` |

## How to Suppress

Add `# noqa: DOEFF014` to the line with the `try` keyword:

```python
def program():
    try:  # noqa: DOEFF014
        return risky_operation()
    except Exception:
        return None
```

Or suppress for the entire file at the top:

```python
# noqa: DOEFF014
```

## Related Effects

- **`Safe(program)`** - Wraps execution and returns `Result[T]`
- **`Catch(program, handler)`** - Catches exceptions and handles them with a handler function
- **`Recover(program, fallback)`** - Provides fallback value/program on error
- **`Retry(program, max_attempts)`** - Retries on failure
- **`Finally(program, cleanup)`** - Ensures cleanup runs even on error

## Configuration

This rule has no configuration options.

## See Also

- [Error Handling Documentation](../../docs/05-error-handling.md)
- [GitHub Issue #2](https://github.com/proboscis/doeff/issues/2) - Native try-except support
