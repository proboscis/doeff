# Cache System

doeff provides a comprehensive caching system with policy-based cache management.

> **Note**: For detailed cache system documentation including policy fields, lifecycle hints, storage options, and custom handlers, see **[cache.md](cache.md)**.

## Quick Overview

### Cache Effects

- **`CacheGet(key)`** - Retrieve cached value (raises KeyError on miss)
- **`CachePut(key, value, **policy)`** - Store value with policy hints

### Cache Decorator

```python
from doeff import cache, do

@cache(ttl=60, lifecycle=CacheLifecycle.SESSION)
@do
def expensive_computation(x: int):
    yield Log("Computing (this should only happen once)...")
    # Expensive work here
    return x * 2

# First call computes
result1 = yield expensive_computation(5)  # Computes

# Second call uses cache
result2 = yield expensive_computation(5)  # From cache
```

### Manual Cache Control

```python
@do
def with_manual_cache():
    try:
        # Try to get from cache
        value = yield CacheGet("my_key")
        yield Log("Cache hit!")
    except KeyError:
        # Cache miss - compute and store
        yield Log("Cache miss, computing...")
        value = yield expensive_operation()
        
        yield CachePut(
            "my_key",
            value,
            ttl=300,  # 5 minutes
            lifecycle=CacheLifecycle.PERSISTENT,
            storage=CacheStorage.DISK
        )
    
    return value
```

## Cache Policy Fields

| Field | Type | Purpose |
|-------|------|---------|
| `ttl` | int | Time-to-live in seconds |
| `lifecycle` | CacheLifecycle | SESSION, PERSISTENT, TEMPORARY |
| `storage` | CacheStorage | MEMORY, DISK, DISTRIBUTED |
| `metadata` | dict | Custom metadata |

See [cache.md](cache.md) for complete policy documentation.

## Common Patterns

### API Response Caching

```python
@cache(ttl=60)
@do
def fetch_user(user_id: int):
    response = yield Await(httpx.get(f"/users/{user_id}"))
    return response.json()
```

### Conditional Caching

```python
@do
def smart_cache(key, fresh=False):
    if fresh:
        # Bypass cache
        value = yield compute_value()
    else:
        safe_result = yield Safe(CacheGet(key))
        if safe_result.is_ok():
            value = safe_result.value
        else:
            value = yield compute_and_cache(key)
    return value
```

## Next Steps

- **[cache.md](cache.md)** - Complete cache system documentation
- **[Patterns](12-patterns.md)** - Cache patterns and best practices
- **[Advanced Effects](09-advanced-effects.md)** - Gather and Atomic effects