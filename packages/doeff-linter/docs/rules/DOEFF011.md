# DOEFF011: No Flag/Mode Arguments

## Summary

Functions and dataclasses should not use flag/mode arguments. Instead, accept a callback or protocol object that implements the varying behavior rather than passing flags/modes/configs and switching with if statements inside.

## Why This Matters

Flag and mode arguments often lead to:

1. **Hidden complexity**: The function contains multiple code paths that are hard to reason about
2. **Violation of Single Responsibility Principle**: The function does multiple things based on flags
3. **Tight coupling**: Callers need to know about internal implementation details
4. **Difficult testing**: Each flag combination needs separate test cases
5. **Poor extensibility**: Adding new modes requires modifying existing code

## What This Rule Detects

### Boolean Flag Arguments

```python
# ❌ Bad: Boolean flag controls behavior
def process_data(data: list, use_cache: bool = True) -> list:
    if use_cache:
        return get_cached(data)
    return compute(data)

# ❌ Bad: Multiple flags
def build(source: str, use_cache: bool = True, enable_minification: bool = False) -> str:
    ...
```

### Literal Mode Arguments

```python
# ❌ Bad: Literal type with few options indicates mode switching
from typing import Literal

def sort_items(items: list, mode: Literal["fast", "safe"]) -> list:
    if mode == "fast":
        return quick_sort(items)
    return merge_sort(items)
```

### Flag-Like Parameter Names

Parameters with names suggesting flags are flagged even without type annotations:

- Prefixes: `is_`, `has_`, `use_`, `enable_`, `disable_`, `with_`, `without_`, `should_`, `can_`, `allow_`, `no_`, `skip_`, `include_`, `exclude_`
- Suffixes: `_enabled`, `_disabled`, `_flag`, `_mode`, `_option`, `_only`, `_first`, `_last`, `_all`, `_none`, `_strict`, `_lenient`
- Common names: `verbose`, `debug`, `quiet`, `silent`, `strict`, `force`, `recursive`, `dry_run`, `mode`, `flag`, etc.

### Dataclass Attributes

```python
# ❌ Bad: Dataclass with flag attributes
@dataclass
class Config:
    name: str
    enable_logging: bool = True
    output_format: Literal["json", "xml", "csv"] = "json"
```

## Recommended Fixes

### Use Callbacks (Callable)

```python
# ✅ Good: Accept a callable that encapsulates the behavior
from typing import Callable

def sort_items(items: list, sorter: Callable[[list], list]) -> list:
    return sorter(items)

# Usage:
sorted_items = sort_items(items, quick_sort)
sorted_items = sort_items(items, merge_sort)
```

### Use Protocol Objects

```python
# ✅ Good: Accept a protocol object with method implementations
from typing import Protocol

class CacheProtocol(Protocol):
    def get(self, data: list) -> list | None:
        ...
    
    def store(self, data: list, result: list) -> None:
        ...

class MemoryCache:
    def get(self, data: list) -> list | None:
        return self._cache.get(hash(tuple(data)))
    
    def store(self, data: list, result: list) -> None:
        self._cache[hash(tuple(data))] = result

class NoCache:
    def get(self, data: list) -> list | None:
        return None
    
    def store(self, data: list, result: list) -> None:
        pass

def process_data(data: list, cache: CacheProtocol) -> list:
    if cached := cache.get(data):
        return cached
    result = compute(data)
    cache.store(data, result)
    return result
```

### For Dataclasses

```python
# ✅ Good: Store protocol objects or callables instead of flags
from dataclasses import dataclass, field
from typing import Protocol

class OutputFormatter(Protocol):
    def format(self, data: dict) -> str:
        ...

class JsonFormatter:
    def format(self, data: dict) -> str:
        return json.dumps(data)

class XmlFormatter:
    def format(self, data: dict) -> str:
        return dict_to_xml(data)

@dataclass
class Config:
    name: str
    formatter: OutputFormatter = field(default_factory=JsonFormatter)
```

## Severity

- **Warning**: For boolean flags with flag-like names and Literal type modes
- **Info**: For flag-like names without type annotations (heuristic detection)

## Configuration

This rule is enabled by default. To disable:

```toml
# pyproject.toml
[tool.doeff-linter]
disabled_rules = ["DOEFF011"]
```

## Related Patterns

- [Python Protocols](https://docs.python.org/3/library/typing.html#typing.Protocol)
- [Callable Type Hints](https://docs.python.org/3/library/typing.html#typing.Callable)
- [Replace Conditional with Polymorphism](https://refactoring.guru/replace-conditional-with-polymorphism)
- [Higher-Order Functions](https://en.wikipedia.org/wiki/Higher-order_function)

## See Also

- DOEFF005: No Setter Methods (related immutability principle)
- DOEFF006: No Tuple Returns (related to clean interfaces)

