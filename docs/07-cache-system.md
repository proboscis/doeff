# Cache System

doeff provides a caching system with policy-based cache management via the `doeff_core_effects` package.

> **Note**: For detailed cache system documentation including policy fields, lifecycle hints, storage options, and custom handlers, see **[cache.md](cache.md)**.

## Quick Overview

### Cache Effects

- **`CacheGet(key)`** - Retrieve cached value (raises KeyError on miss)
- **`CachePut(key, value, **policy)`** - Store value with policy hints

### Import

```python
from doeff_core_effects.cache_effects import CacheGet, CachePut
from doeff_core_effects.cache_policy import CacheLifecycle, CacheStorage
```

### Manual Cache Control

```python
from doeff import do
from doeff_core_effects import Tell
from doeff_core_effects.cache_effects import CacheGet, CachePut
from doeff_core_effects.cache_policy import CacheLifecycle, CacheStorage

@do
def with_manual_cache():
    try:
        # Try to get from cache
        value = yield CacheGet("my_key")
        yield Tell("Cache hit!")
    except KeyError:
        # Cache miss - compute and store
        yield Tell("Cache miss, computing...")
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
| `ttl` | float | Time-to-live in seconds |
| `lifecycle` | CacheLifecycle | TRANSIENT, SESSION, PERSISTENT |
| `storage` | CacheStorage | MEMORY, DISK |
| `metadata` | dict | Custom metadata |

See [cache.md](cache.md) for complete policy documentation.

## Common Patterns

### API Response Caching

```python
from doeff import do
from doeff_core_effects.cache_effects import CacheGet, CachePut

@do
def fetch_user(user_id: int):
    try:
        return (yield CacheGet(f"user_{user_id}"))
    except KeyError:
        response = yield Await(httpx.get(f"/users/{user_id}"))
        data = response.json()
        yield CachePut(f"user_{user_id}", data, ttl=60)
        return data
```

### Conditional Caching

```python
from doeff import Try, do
from doeff_core_effects.cache_effects import CacheGet, CachePut

@do
def smart_cache(key, fresh=False):
    if fresh:
        # Bypass cache
        value = yield compute_value()
    else:
        safe_result = yield Try(CacheGet(key))
        if safe_result.is_ok():
            value = safe_result.value
        else:
            value = yield compute_and_cache(key)
    return value
```

## Next Steps

- **[cache.md](cache.md)** - Complete cache system documentation
- **[Patterns](12-patterns.md)** - Cache patterns and best practices
- **[Advanced Effects](09-advanced-effects.md)** - Spawn, Gather, and concurrency effects
