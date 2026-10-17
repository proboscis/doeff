# Cache System

doeff provides a caching system with policy-based cache management via the `doeff_core_effects` package.

> **Note**: For the signatures of the cache effects and the policy enums, see **[Cache Effects in the API Reference](13-api-reference.md#cache-effects)**.

## Quick Overview

### Cache Effects

- **`CacheGet(key)`** - Retrieve cached value (raises KeyError on miss)
- **`CachePut(key, value, **policy)`** - Store value with policy hints

### Import

```python excerpt
from doeff_core_effects.cache_effects import CacheGet, CachePut
from doeff_core_effects.cache_policy import CacheLifecycle, CacheStorage
```

### Manual Cache Control

```python excerpt
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

See [CachePolicy in the API Reference](13-api-reference.md#cachepolicy) for the policy enums.

## Common Patterns

### API Response Caching

On a miss, `CacheGet` raises `KeyError`, so the program fetches the response and stores it; the
next call for the same key is served from the cache. `Await` needs an awaitable, so the request goes
through `httpx.AsyncClient` (`httpx.get` is synchronous). The program runs under a cache handler
(here `in_memory_cache_handler()`) and `await_handler()` inside `scheduled()`.

```python
import httpx
from doeff import Await, do, run
from doeff_core_effects.cache_effects import CacheGet, CachePut
from doeff_core_effects.cache_handlers import in_memory_cache_handler
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled

requested = []

def users_api(request: httpx.Request) -> httpx.Response:
    # Stands in for the real server so the example runs offline (drop transport= to call it).
    requested.append(request.url.path)
    return httpx.Response(200, json={"id": 1, "name": "Ada"})

async def get_json(path: str):
    async with httpx.AsyncClient(
        base_url="https://api.example.com", transport=httpx.MockTransport(users_api)
    ) as client:
        response = await client.get(path)
        response.raise_for_status()
        return response.json()

@do
def fetch_user(user_id: int):
    try:
        return (yield CacheGet(f"user_{user_id}"))
    except KeyError:
        data = yield Await(get_json(f"/users/{user_id}"))
        yield CachePut(f"user_{user_id}", data, ttl=60)
        return data

@do
def program():
    first = yield fetch_user(1)
    second = yield fetch_user(1)  # served from the cache
    return first, second

prog = in_memory_cache_handler()(program())
first, second = run(scheduled(await_handler()(prog)))
print(first)  # {'id': 1, 'name': 'Ada'}
print(second == first)  # True
print(requested)  # ['/users/1']
```

### Conditional Caching

```python excerpt
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

- **[API Reference](13-api-reference.md#cache-effects)** - Cache effect signatures and policy enums
- **[Patterns](12-patterns.md)** - Cache patterns and best practices
- **[Advanced Effects](09-advanced-effects.md)** - Spawn, Gather, and concurrency effects
