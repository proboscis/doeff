# DOEFF022: Prefer @do Decorated Functions

## Summary

Functions should use the `@do` decorator to enable doeff's structured effects, including logging with `yield slog` and composition with other Programs.

## Why This Matters

In doeff, the `@do` decorator transforms generator functions into composable Programs with powerful capabilities:

1. **Effect Tracking**: Side effects (IO, async, etc.) are explicitly tracked
2. **Structured Logging**: `yield slog("message", key=value)` provides context-aware logging
3. **Composability**: @do functions return Programs that can be easily composed
4. **Testability**: Effects can be mocked or intercepted for testing

### The Problem with Regular Functions

```python
# ❌ Regular function: side effects are implicit
def process_data(data: Data) -> Result:
    print(f"Processing {len(data)} items")  # Implicit IO
    result = expensive_computation(data)
    return result
```

This pattern has issues:

- **Hidden Side Effects**: The `print` statement is a side effect that's not tracked
- **Limited Composition**: Can't easily combine with other async/effect-ful operations
- **No Structured Logging**: Plain print statements don't support structured metadata
- **Testing Difficulty**: Hard to capture or verify the log output in tests

## What This Rule Detects

### Functions Without @do Decorator

```python
# ❌ Bad: No @do decorator
def process_data(data: Data) -> Result:
    return Result()

# ❌ Bad: Async function without @do
async def fetch_data(url: str) -> Data:
    return Data()

# ❌ Bad: Public method without @do
class Processor:
    def process(self, data: Data) -> Result:
        return Result()
```

## Recommended Fixes

### Add @do Decorator with Structured Logging

```python
# ✅ Good: Using @do with structured logging
from doeff import do, EffectGenerator
from doeff.effects import slog

@do
def process_data(data: Data) -> EffectGenerator[Result]:
    yield slog("Processing data", count=len(data), data_type=type(data).__name__)
    result = expensive_computation(data)
    yield slog("Processing complete", result_size=len(result))
    return result
```

### Async Functions

```python
# ✅ Good: Async function with @do
@do
async def fetch_data(url: str) -> EffectGenerator[Data]:
    yield slog("Fetching data", url=url)
    data = await http_client.get(url)
    yield slog("Fetch complete", bytes=len(data))
    return Data(data)
```

### Methods in Classes

```python
# ✅ Good: Public method with @do
class DataProcessor:
    @do
    def process(self, data: Data) -> EffectGenerator[Result]:
        yield slog("Starting processing", processor=self.__class__.__name__)
        result = self._transform(data)
        return result
```

## Allowed Patterns (Skipped by This Rule)

The rule does NOT flag these patterns:

```python
# ✅ Dunder methods (always skipped)
class MyClass:
    def __init__(self, value: int):
        self.value = value
    
    def __str__(self) -> str:
        return str(self.value)

# ✅ Test functions (always skipped)
def test_my_feature():
    assert True

# ✅ Private methods (skipped as internal implementation)
class MyClass:
    def _internal_helper(self) -> int:
        return 42

# ✅ Property decorators (skipped)
class MyClass:
    @property
    def value(self) -> int:
        return self._value

# ✅ Static/class methods (skipped)
class MyClass:
    @staticmethod
    def create() -> MyClass:
        return MyClass()
    
    @classmethod
    def from_dict(cls, data: dict) -> MyClass:
        return cls()

# ✅ Abstract methods (skipped)
class AbstractProcessor:
    @abstractmethod
    def process(self) -> Result:
        pass

# ✅ main() function (common entry point, skipped)
def main():
    run_app()

# ✅ pytest fixtures (skipped)
@pytest.fixture
def sample_data():
    return Data()

# ✅ setUp/tearDown (unittest lifecycle, skipped)
class TestCase:
    def setUp(self):
        pass
```

## Using yield slog for Structured Logging

The `slog` effect provides structured, context-aware logging:

```python
from doeff.effects import slog

@do
def complex_operation(data: Data) -> EffectGenerator[Result]:
    # Basic logging
    yield slog("Starting operation")
    
    # With structured metadata
    yield slog("Processing batch", 
               batch_size=len(data),
               source=data.source,
               timestamp=datetime.now().isoformat())
    
    # Error context
    if not data.is_valid():
        yield slog("Invalid data detected", 
                   level="warning",
                   validation_errors=data.errors)
    
    return process(data)
```

Benefits of `yield slog`:

- Structured key-value pairs for log aggregation systems
- Context propagation through Program composition
- Can be intercepted/captured in tests
- Consistent logging format across the application

## Severity

**Info** - This is a best practice recommendation, not a hard error. The rule encourages adoption of the doeff pattern but allows suppression for legitimate cases.

## Configuration

This rule is enabled by default but can be disabled:

```toml
# pyproject.toml
[tool.doeff-linter]
disabled_rules = ["DOEFF022"]
```

## Suppressing the Rule

If a function intentionally doesn't use doeff effects, add a noqa comment:

```python
def pure_computation(x: int, y: int) -> int:  # noqa: DOEFF022
    """Pure function with no side effects - @do not needed."""
    return x + y
```

## Related Rules

- **DOEFF017**: No Program Type in Parameters (related: @do function parameter types)
- **DOEFF009**: Missing Return Type Annotation (related: type safety)
- **DOEFF015**: No Zero-Argument Program Entrypoints (related: Program construction)

## See Also

- [Getting Started with doeff](../../docs/01-getting-started.md)
- [Core Concepts: Effects and Programs](../../docs/02-core-concepts.md)
- [Structured Logging](../../docs/07-cache-system.md)

