# Class 2, scheduling: k escapes the handler. In-flight deduplication (docs/22-capability-classes.md, "Non-obvious
# members of class 2"): the second identical request parks its k until the first call completes, then both resume
# with the same value. Three tasks ask for "prices", "prices" and "news"; the backend takes 2 s per call, on the
# virtual clock, so the times are the same on every run. Prints when each request starts, parks and resumes.
from dataclasses import dataclass
from datetime import datetime, timezone

from doeff_core_effects import CompletePromise, CreatePromise, Gather, Spawn, Wait, scheduled
from doeff_time import Delay, GetTime, sim_time_handler

from doeff import EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Fetch(EffectBase):
    key: str


LINES = []
CALLS = []


@do
def note(text):
    now = yield GetTime()
    LINES.append(f"{now:%H:%M:%S.%f}"[:-5] + f" {text}")


@handler
@do(non_tail=True)
def backend(effect: Fetch, k):
    CALLS.append(effect.key)
    yield note(f"backend starts {effect.key!r}")
    yield Delay(2)
    yield note(f"backend returns {effect.key!r}")
    return (yield Resume(k, f"{effect.key}@v1"))


def make_dedup():
    in_flight = {}

    @handler
    @do(non_tail=True)
    def dedup(effect: Fetch, k):
        if effect in in_flight:
            yield note(f"dedup parks k: {effect.key!r} is in flight")
            value = yield Wait(in_flight[effect].future)  # k is parked here, not resumed
            yield note(f"dedup resumes the parked k with {value!r}")
            return (yield Resume(k, value))
        promise = yield CreatePromise()
        in_flight[effect] = promise
        value = yield effect  # the one real call
        yield CompletePromise(promise, value)
        return (yield Resume(k, value))

    return dedup


@do
def request(name, key, after):
    yield Delay(after)
    yield note(f"{name} asks {key!r}")
    value = yield Fetch(key)
    yield note(f"{name} gets {value!r}")
    return value


@do
def main():
    tasks = []
    for name, key, after in [("task 1", "prices", 0), ("task 2", "prices", 0.5), ("task 3", "news", 1)]:
        tasks.append((yield Spawn(request(name, key, after))))
    return (yield Gather(*tasks))


START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
answers = run(scheduled(sim_time_handler(start_time=START)(backend(make_dedup()(main())))))
for line in LINES:
    print(line)
print(f"answers: {answers!r}")
print(f"backend calls: {CALLS!r}")
