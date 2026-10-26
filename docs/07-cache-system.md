# Cache System

doeff provides a caching system with policy-based cache management via the `doeff_core_effects` package.

[![A narrated video, about two and a half minutes: CacheGet and CachePut as effects a cache handler answers; a miss thrown into the program as KeyError at the yield, then a hit in 0.5 ms instead of 408.2 ms; the policy travelling inside CachePut as hints the handler reads; the page's API example sending one request for two calls; and the same program under in_memory_cache_handler, sqlite_cache_handler and no cache handler](assets/videos/cache-system.jpg)](assets/videos/cache-system.mp4)

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
next call for the same key is served from the cache. The fetch is an effect too: `fetch_user` yields
`GetJson(path)` and does no I/O of its own; the handler it runs under decides what `GetJson` means.
Under `answer_from_server`, `GetJson` means "GET the path from the server with httpx". Under
`answer_from_memory`, it means "answer from memory and record the path", so the example runs
offline. To call the server, run the program under `answer_from_server`; `fetch_user` does not change.
The program runs under a cache handler (here `in_memory_cache_handler()`) and `await_handler()`
inside `scheduled()`.

```python
from dataclasses import dataclass

import httpx
from doeff import EffectBase, Pass, Resume, do, handler, run
from doeff_core_effects.cache_effects import CacheGet, CachePut
from doeff_core_effects.cache_handlers import in_memory_cache_handler
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled

@dataclass(frozen=True)
class GetJson(EffectBase):  # an effect: "GET this path and give me the JSON body"
    path: str

@handler
def answer_from_server(effect, k):  # GetJson means: GET the path from the server
    if isinstance(effect, GetJson):
        response = httpx.get(f"https://api.example.com{effect.path}")
        response.raise_for_status()
        return (yield Resume(k, response.json()))
    return (yield Pass(effect, k))

requested = []

@handler
def answer_from_memory(effect, k):  # GetJson means: answer from memory, record the path
    if isinstance(effect, GetJson):
        requested.append(effect.path)
        return (yield Resume(k, {"id": 1, "name": "Ada"}))
    return (yield Pass(effect, k))

@do
def fetch_user(user_id: int):
    try:
        return (yield CacheGet(f"user_{user_id}"))
    except KeyError:
        data = yield GetJson(f"/users/{user_id}")
        yield CachePut(f"user_{user_id}", data, ttl=60)
        return data

@do
def program():
    first = yield fetch_user(1)
    second = yield fetch_user(1)  # served from the cache
    return first, second

prog = in_memory_cache_handler()(answer_from_memory(program()))
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
