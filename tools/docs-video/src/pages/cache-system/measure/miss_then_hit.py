# The miss-and-hit scene: the page's with_manual_cache(), called twice under in_memory_cache_handler().
# The first CacheGet finds nothing: the handler throws KeyError into the program at the yield, the program
# computes the value (0.4 s of work) and stores it with CachePut. The handler reaches its storage through Await,
# so the run has await_handler() inside scheduled(), as on the page. The second CacheGet is answered from the cache.
# Prints every cache effect the program yields, the log, and the wall time of each call
# (docs/07-cache-system.md, "Manual Cache Control").
import time

from doeff_core_effects import Tell
from doeff_core_effects.cache_effects import CacheGet, CachePut
from doeff_core_effects.cache_handlers import in_memory_cache_handler
from doeff_core_effects.cache_policy import CacheLifecycle, CacheStorage
from doeff_core_effects.handlers import await_handler, state, writer, writer_log
from doeff_core_effects.scheduler import scheduled

from doeff import WithObserve, do, run


@do
def expensive_operation():
    time.sleep(0.4)  # stands in for real work
    return 42


@do
def with_manual_cache():
    try:
        value = yield CacheGet("my_key")
        yield Tell("Cache hit!")
    except KeyError as miss:
        print("  KeyError at the yield:", repr(miss))
        yield Tell("Cache miss, computing...")
        value = yield expensive_operation()
        yield CachePut(
            "my_key",
            value,
            ttl=300,
            lifecycle=CacheLifecycle.PERSISTENT,
            storage=CacheStorage.DISK,
        )
    return value


@do
def twice():
    times = []
    for _ in range(2):
        start = time.perf_counter()
        value = yield with_manual_cache()
        times.append((value, (time.perf_counter() - start) * 1000))
    return times, (yield writer_log())


def seen(effect):
    if type(effect).__name__.startswith("Cache"):
        print("yielded:", effect)


prog = in_memory_cache_handler()(WithObserve(seen, twice()))
times, log = run(scheduled(await_handler()(state()(writer(prog)))))
for i, (value, ms) in enumerate(times, 1):
    print(f"call {i}: returns {value} in {ms:.1f} ms")
print("log:", log)
