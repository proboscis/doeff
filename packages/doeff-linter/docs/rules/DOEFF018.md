# DOEFF018: No Ask Effect Inside Try-Except Blocks

## Summary

Detects `yield ask(...)` calls inside try/except blocks. DI failures indicate a programming error (missing dependency injection) and should never be caught at runtime.

## Rationale

The `ask` effect in doeff is used for dependency injection. When `ask` fails, it means the required dependency was not properly configured in the DI container. This is a **programming error**, not a runtime error that should be handled gracefully.

Catching `ask` failures with try/except:

1. **Masks configuration errors** - The real problem (missing DI configuration) is hidden
2. **Leads to unexpected behavior** - Fallback values may cause subtle bugs
3. **Violates DI principles** - Dependencies should be guaranteed at composition time
4. **Defeats type safety** - The dependency graph should be verified before execution

## Examples

### Bad

```python
# Pattern 1: Simple ask in try
@do
def get_config():
    try:
        value = yield ask("config_key")  # DOEFF018: ask effect in try block
    except:
        value = "default"
    return value

# Pattern 2: Multiple asks in try
@do
def load_services():
    try:
        db = yield ask("database")       # DOEFF018
        cache = yield ask("cache")       # DOEFF018
    except:
        db = None
        cache = None

# Pattern 3: Nested try with ask
@do
def nested_ask():
    try:
        try:
            config = yield ask("nested")  # DOEFF018
        except ConfigError:
            config = None
    except OuterError:
        pass

# Pattern 4: Ask in try inside loop
@do
def ask_in_loop():
    try:
        for item in items:
            processor = yield ask("processor")  # DOEFF018
            yield processor(item)
    except:
        pass
```

### Good

```python
from doeff import do, ask

# Pattern 1: Ask outside try block
@do
def get_config():
    # Dependencies resolved first (guaranteed by DI)
    value = yield ask("config_key")
    
    # Risky operations can use try/except (but prefer Safe effect)
    try:
        result = process(value)
    except ProcessError:
        result = "default"
    return result

# Pattern 2: Proper DI configuration
@do
def load_services():
    # All dependencies resolved upfront
    db = yield ask("database")
    cache = yield ask("cache")
    
    # Use the dependencies safely
    return ServiceContainer(db=db, cache=cache)

# Pattern 3: Use default parameters for optional dependencies
@do
def configurable_service(fallback_value: str = "default"):
    # If config might not exist, use function parameters
    config = yield ask("config_key")  # Will always succeed if DI is correct
    return config or fallback_value

# Pattern 4: Design proper DI hierarchy
# In your DI setup:
injector = Injector({
    "config_key": provide_config,  # Ensure all dependencies are registered
    "database": provide_database,
    "cache": provide_cache,
})
```

## When This Rule Doesn't Apply

This rule is very strict by design. The `ask` effect should **never** be used inside try/except blocks in normal code. However, you might suppress this rule in:

### 1. Migration/Transition Code

When migrating legacy code to doeff:

```python
@do
def legacy_migration():  # noqa: DOEFF018
    # Temporary: will be refactored once all DI is set up
    try:
        new_service = yield ask("new_service")
    except:
        new_service = LegacyServiceAdapter()
```

### 2. Testing/Debugging

In test utilities that intentionally check DI behavior:

```python
@do
def test_missing_dependency():  # noqa: DOEFF018
    try:
        value = yield ask("nonexistent_key")
        assert False, "Should have raised"
    except KeyError:
        pass  # Expected
```

## Difference from DOEFF014

| Rule | What it detects | Severity |
|------|-----------------|----------|
| DOEFF014 | All try/except blocks | Warning |
| DOEFF018 | `yield ask(...)` inside try blocks | Error |

DOEFF014 is a general guideline to prefer effect-based error handling.
DOEFF018 is a strict rule because DI failures are always programming errors.

## How to Suppress

Add `# noqa: DOEFF018` to the line with the `yield ask(...)`:

```python
@do
def temporary_workaround():
    try:
        value = yield ask("key")  # noqa: DOEFF018
    except:
        value = fallback()
```

Or suppress for the entire file at the top:

```python
# noqa: DOEFF018
```

## The Right Fix

Instead of catching `ask` failures, fix your DI configuration:

```python
# ❌ Wrong: Catching missing DI
@do
def get_database():
    try:
        db = yield ask("database")
    except:
        db = create_default_database()  # Hides the real problem
    return db

# ✅ Right: Proper DI setup
# In your application setup:
injector = Injector({
    "database": provide_database,  # Always provide the dependency
})

@do
def get_database():
    db = yield ask("database")  # Will always succeed
    return db
```

## Configuration

This rule has no configuration options.

## See Also

- [DOEFF014: No Try-Except Blocks](./DOEFF014.md)
- [Dependency Injection with ask](../../docs/03-basic-effects.md#ask-effect)
- [Error Handling Documentation](../../docs/05-error-handling.md)

