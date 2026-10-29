# The policy scene: CachePut carries its policy as part of the effect. Prints the CachePolicy each CachePut holds
# when the handler receives it, the storage each policy resolves to, and what in_memory_cache_handler() does with
# a ttl: it keeps the policy as a hint and stores every entry the same way (docs/07-cache-system.md,
# "Cache Policy Fields").
from doeff_core_effects import await_handler, scheduled
from doeff_core_effects.cache_effects import CacheGet, CachePut
from doeff_core_effects.cache_handlers import in_memory_cache_handler
from doeff_core_effects.cache_policy import CacheLifecycle, CacheStorage
from doeff_time import Delay, sync_time_handler

from doeff import WithObserve, do, run

PUTS = [
    CachePut("my_key", 42, ttl=300, lifecycle=CacheLifecycle.PERSISTENT, storage=CacheStorage.DISK),
    CachePut("user_1", {"id": 1}, ttl=60),
    CachePut("report", "...", lifecycle=CacheLifecycle.PERSISTENT),
    CachePut("tmp", "...", metadata={"source": "api"}),
]


def seen(effect):
    if type(effect).__name__ == "CachePutEffect":
        policy = effect.policy
        print(f"handler gets {effect!r} with policy:")
        print(f"  ttl={policy.ttl} lifecycle={policy.lifecycle.name} storage={policy.storage and policy.storage.name}"
              f" metadata={dict(policy.metadata)} -> resolved_storage()={policy.resolved_storage().name}")


@do
def store_all():
    for put in PUTS:
        yield put


@do
def ttl_hint():
    yield CachePut("short", "value", ttl=0.1)
    yield Delay(0.3)
    return (yield CacheGet("short"))


def under_cache(prog):
    return run(scheduled(sync_time_handler()(await_handler()(in_memory_cache_handler()(prog)))))


under_cache(WithObserve(seen, store_all()))
print("in_memory_cache_handler, ttl=0.1, read 0.3 s later:", repr(under_cache(ttl_hint())))
