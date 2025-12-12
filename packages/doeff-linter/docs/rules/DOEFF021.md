# DOEFF021: No __all__ Declaration

## Summary

Forbid the use of `__all__` in Python modules. This project defaults to exporting everything from modules.

## Why This Matters

In this project, all module-level definitions are considered part of the public API by default. The `__all__` variable is typically used to restrict what gets exported when using `from module import *`, but this pattern conflicts with our design philosophy:

1. **Explicit is better than restrictive**: Rather than hiding exports, make them obviously named
2. **Discoverability**: All module contents should be easily discoverable
3. **Consistency**: A uniform export policy across all modules simplifies maintenance

### The Problem with __all__

```python
# ❌ Bad: Restricts exports
__all__ = ["PublicClass", "public_function"]

class PublicClass:
    pass

class InternalClass:  # This won't be exported via *
    pass

def public_function():
    pass

def _helper():  # Underscore prefix already indicates internal use
    pass
```

When `__all__` is used:
- Developers may forget to add new public symbols
- The list can become out of sync with actual exports
- It adds maintenance burden for little benefit

## What This Rule Detects

### Basic Assignment

```python
# ❌ Bad: Simple assignment
__all__ = ["foo", "bar"]
```

### Annotated Assignment

```python
# ❌ Bad: With type annotation
__all__: list = ["foo", "bar"]
__all__: list[str] = ["foo", "bar"]
```

### Augmented Assignment

```python
# ❌ Bad: Extending __all__
__all__ += ["additional_export"]
```

### Empty __all__

```python
# ❌ Bad: Even empty __all__ is forbidden
__all__ = []
```

## Recommended Fixes

### Use Underscore Prefix for Internal Items

```python
# ✅ Good: Use naming conventions instead of __all__
class PublicClass:
    """This is part of the public API."""
    pass

class _InternalClass:
    """Underscore prefix indicates this is internal."""
    pass

def public_function():
    """Public API."""
    pass

def _helper():
    """Internal helper function."""
    pass
```

### Remove __all__ Entirely

```python
# Before (bad):
__all__ = ["MyClass", "my_function"]

class MyClass:
    pass

def my_function():
    pass

# After (good):
class MyClass:
    pass

def my_function():
    pass

# Everything is exported, use underscore prefix for internal items
```

## Suppressing This Rule

If you have a specific reason to use `__all__`, you can suppress this rule with a noqa comment:

```python
# noqa: DOEFF021 - Reason: This module is part of a third-party API that requires __all__
__all__ = ["specific_export"]
```

Always include a comment explaining why the exception is necessary.

## Severity

**Error** - This violates the project's export policy.

## Configuration

This rule is enabled by default. To disable:

```toml
# pyproject.toml
[tool.doeff-linter]
disabled_rules = ["DOEFF021"]
```

## Related Patterns

- Use `_` prefix for module-internal functions and classes
- Use `__` prefix for name-mangled class attributes (Python standard)
- Use clear, descriptive names that indicate intended usage

## See Also

- DOEFF016: No Relative Imports (related: module organization)
- Python documentation on [__all__](https://docs.python.org/3/tutorial/modules.html#importing-from-a-package)

