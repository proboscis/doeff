# The handler scene: one program, run twice (two separate run() calls) under each cache handler, and once with no
# cache handler at all. in_memory_cache_handler() starts empty in every run, so both runs compute; with
# sqlite_cache_handler(path) the second run reads what the first one stored. Prints, for each stack, what each run
# did, its wall time, and how many times the 0.4 s of work ran (docs/07-cache-system.md).
import tempfile
import time
from pathlib import Path

from doeff_core_effects.cache_effects import CacheGet, CachePut
from doeff_core_effects.cache_handlers import in_memory_cache_handler, sqlite_cache_handler
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled

from doeff import do, run

COMPUTED = []


@do
def expensive_operation():
    COMPUTED.append(1)
    time.sleep(0.4)  # stands in for real work
    return 42


@do
def cached_answer():
    try:
        return "hit", (yield CacheGet("answer"))
    except KeyError:
        value = yield expensive_operation()
        yield CachePut("answer", value)
        return "miss", value


def twice(make_handler):
    COMPUTED.clear()
    for i in (1, 2):
        start = time.perf_counter()
        how, value = run(scheduled(await_handler()(make_handler()(cached_answer()))))
        print(f"  run {i}: {how} -> {value} in {(time.perf_counter() - start) * 1000:.1f} ms")
    print(f"  the work ran {len(COMPUTED)} time(s)")


print("in_memory_cache_handler():")
twice(in_memory_cache_handler)
db = Path(tempfile.mkdtemp()) / "cache.sqlite"
print("sqlite_cache_handler('cache.sqlite'):")
twice(lambda: sqlite_cache_handler(db))
print("no cache handler:")
try:
    run(scheduled(await_handler()(cached_answer())))
except Exception as error:
    print(f"  {type(error).__name__}: {str(error).splitlines()[0]}")
