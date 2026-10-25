# The API scene: the page's "API Response Caching" example as it is on the page. fetch_user(1) is called twice; the
# stand-in server records every request it gets, and only the first call reaches it (docs/07-cache-system.md).
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
