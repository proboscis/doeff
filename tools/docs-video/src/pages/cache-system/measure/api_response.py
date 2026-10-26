# The API scene: the page's "API Response Caching" example as it is on the page. fetch_user(1) is called twice;
# fetch_user yields GetJson, and the handler answer_from_memory gives GetJson its meaning here: answer from memory
# and record every path it is asked for. Only the first call reaches it (docs/07-cache-system.md).
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
