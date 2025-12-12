# DOEFF013: Prefer Maybe Monad

## Summary

Detects `Optional[X]` or `X | None` type annotations and suggests using doeff's `Maybe` monad instead for explicit null handling.

## Rationale

In Python, `Optional[X]` and `X | None` are commonly used to represent values that may or may not be present. However, these types have several drawbacks:

1. **Null pointer exceptions** - It's easy to forget to check for `None`, leading to runtime errors
2. **Implicit handling** - Nothing forces you to handle the `None` case
3. **Poor composability** - Chaining operations on optional values requires verbose `if` checks
4. **No type-safe unwrapping** - You can call `.attribute` on a potentially `None` value without type errors

The `Maybe` monad from doeff provides:

1. **Explicit presence tracking** - `Some(value)` vs `NOTHING` makes state clear
2. **Safe operations** - `.map()`, `.flat_map()`, `.filter()` handle `None` automatically
3. **Forced handling** - `.unwrap()`, `.unwrap_or()`, `.expect()` make you deal with absence
4. **Composability** - Chain operations without nested `if` statements
5. **Integration with Result** - `.ok_or()` converts to `Result` for error handling

## Examples

### Bad

```python
from typing import Optional, Union

# Pattern 1: Optional return type
def find_user(id: int) -> Optional[User]:
    user = db.get(id)
    return user

# Pattern 2: Union with None (Python 3.10+ syntax)
def get_config_value(key: str) -> str | None:
    return config.get(key)

# Pattern 3: Optional parameter
def process(data: Optional[list[int]] = None) -> int:
    if data is None:
        return 0
    return sum(data)

# Pattern 4: Variable annotation
result: Optional[str] = None

# Pattern 5: typing.Optional qualified
def get_name() -> typing.Optional[str]:
    return None

# Pattern 6: Union[X, None]
def fetch() -> Union[Response, None]:
    return None
```

### Good

```python
from doeff import Maybe, Some, NOTHING

# Pattern 1: Maybe return type
def find_user(id: int) -> Maybe[User]:
    user = db.get(id)
    return Maybe.from_optional(user)

# Pattern 2: Explicit Some/NOTHING
def get_config_value(key: str) -> Maybe[str]:
    value = config.get(key)
    if value is not None:
        return Some(value)
    return NOTHING

# Pattern 3: Maybe parameter with default handling
def process(data: Maybe[list[int]]) -> int:
    return data.map(sum).unwrap_or(0)

# Pattern 4: Variable with Maybe
result: Maybe[str] = NOTHING

# Pattern 5: Chaining operations safely
def get_user_email(id: int) -> Maybe[str]:
    return (
        find_user(id)
        .map(lambda u: u.profile)
        .flat_map(lambda p: Maybe.from_optional(p.email))
    )

# Pattern 6: Converting to Result for error handling
def get_required_user(id: int) -> Result[User]:
    return find_user(id).ok_or(UserNotFoundError(id))

# Pattern 7: Pattern matching with Maybe
def greet_user(user: Maybe[User]) -> str:
    match user:
        case Some(u):
            return f"Hello, {u.name}!"
        case _:
            return "Hello, guest!"
```

## Maybe API Reference

### Creating Maybe values

```python
from doeff import Maybe, Some, NOTHING

# From a value
present = Some(42)           # Maybe[int] containing 42
absent = NOTHING             # Singleton representing absence

# From Optional value
maybe = Maybe.from_optional(value)  # Some(value) if not None, else NOTHING
```

### Checking presence

```python
maybe.is_some()  # True if contains value
maybe.is_none()  # True if NOTHING
bool(maybe)      # Same as is_some()
```

### Extracting values

```python
maybe.unwrap()              # Returns value or raises RuntimeError
maybe.expect("custom msg")  # Returns value or raises with message
maybe.unwrap_or(default)    # Returns value or default
maybe.unwrap_or_else(fn)    # Returns value or calls fn()
maybe.to_optional()         # Converts back to Python Optional
```

### Transforming values

```python
maybe.map(fn)              # Apply fn if Some, returns Maybe
maybe.flat_map(fn)         # Apply fn that returns Maybe
maybe.filter(predicate)    # NOTHING if predicate fails
```

### Converting to Result

```python
maybe.ok_or(error)         # Ok(value) or Err(error)
maybe.ok_or_else(error_fn) # Ok(value) or Err(error_fn())
```

## Migration Guide

### Simple replacement

```python
# Before
def get_value() -> Optional[int]:
    return None

# After
def get_value() -> Maybe[int]:
    return NOTHING
```

### With existing Optional values

```python
# Before
def process(opt: Optional[str]) -> Optional[str]:
    if opt is None:
        return None
    return opt.upper()

# After
def process(maybe: Maybe[str]) -> Maybe[str]:
    return maybe.map(str.upper)
```

### Converting at boundaries

```python
# External API returns Optional
external_result: Optional[Data] = external_api.fetch()

# Convert to Maybe for internal use
internal: Maybe[Data] = Maybe.from_optional(external_result)

# Process with Maybe operations
processed = internal.map(transform).filter(validate)

# Convert back if needed for external API
external_api.send(processed.to_optional())
```

## When This Rule Doesn't Apply

### 1. External API compatibility

When your function signature must match an external interface:

```python
# Must match Protocol or ABC definition
def callback(value: str | None) -> None:  # noqa: DOEFF013
    pass
```

### 2. Pydantic/dataclass fields

When using with frameworks that expect Optional:

```python
from pydantic import BaseModel

class Config(BaseModel):
    optional_field: Optional[str] = None  # noqa: DOEFF013
```

### 3. JSON serialization boundaries

```python
def to_json(user: User) -> dict:
    return {
        "name": user.name,
        "email": user.email.to_optional(),  # Convert back for JSON
    }
```

## How to Suppress

Add `# noqa: DOEFF013` to the line:

```python
def external_callback(value: str | None) -> None:  # noqa: DOEFF013
    pass
```

Or suppress for the entire file at the top:

```python
# noqa: DOEFF013
```

## Configuration

This rule has no configuration options.

## See Also

- [Maybe Monad Documentation](../../doeff/_vendor.py)
- [Result Type](../../docs/05-error-handling.md)
- [Rust Option Type](https://doc.rust-lang.org/std/option/)
- [Scala Option](https://www.scala-lang.org/api/current/scala/Option.html)
