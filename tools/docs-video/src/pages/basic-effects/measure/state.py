# State: the store lives in the state handler. Get and Put read and write it; the program returns what it read.
# Each state() starts its own store. Backs the State scene of the video: the store after every Put.
from doeff_core_effects import Get, Put
from doeff_core_effects.handlers import state

from doeff import Pass, do, handler, run

STORES = []


@do
def counter_operations():
    yield Put("count", 0)
    for _ in range(3):
        val = yield Get("count")
        yield Put("count", val + 1)
    return (yield Get("count"))


@handler
def watch(effect: Put, k):  # records each Put on its way out to state, then passes it on unchanged
    STORES.append(f"Put({effect.key!r}, {effect.value!r})")
    yield Pass(effect, k)


print("returns:", run(state()(watch(counter_operations()))))
print("puts:", STORES)
print("second run, a new store:", run(state()(counter_operations())))


@do
def read_missing():
    return (yield Get("missing"))


print("Get of a missing key:", run(state()(read_missing())))
