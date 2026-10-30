# The same program under both implementations: the order its tasks run in, what Cancel does, and the trace events.
from doeff_core_effects import (
    Cancel,
    CompletePromise,
    CreatePromise,
    Gather,
    Spawn,
    TaskCancelledError,
    TaskPriority,
    Wait,
    scheduled,
)
from doeff_core_effects._scheduler import set_scheduler_trace

from doeff import do, run

ORDER: list[str] = []


@do
def step(name: str, gate):
    ORDER.append(f"{name} starts")
    value = yield Wait(gate.future)
    ORDER.append(f"{name} wakes with {value}")
    return name.upper()


@do
def stuck(gate):
    try:
        ORDER.append("slow starts")
        yield Wait(gate.future)
    finally:
        ORDER.append("slow finally")


@do
def opener(gate):
    ORDER.append("opener completes the promise")
    yield CompletePromise(gate, 42)


@do
def main():
    gate = yield CreatePromise()
    never = yield CreatePromise()
    a = yield Spawn(step("a", gate))
    b = yield Spawn(step("b", gate), priority=TaskPriority.HIGH)
    slow = yield Spawn(stuck(never))
    yield Spawn(opener(gate))
    results = yield Gather(a, b)
    yield Cancel(slow)
    try:
        yield Wait(slow)
    except TaskCancelledError:
        ORDER.append("slow is cancelled")
    return results


def measure(implementation: str):
    ORDER.clear()
    events: list[str] = []
    set_scheduler_trace(lambda event: events.append(event["event"]))
    try:
        result = run(scheduled(main(), implementation=implementation))
    finally:
        set_scheduler_trace(None)
    return result, list(ORDER), events


runs = {impl: measure(impl) for impl in ("python", "rust")}
for impl, (result, order, events) in runs.items():
    print(f"{impl}: result {result}")
    for line in order:
        print(f"  {line}")
    print(f"  trace: {len(events)} events, kinds {sorted(set(events))}")
python, rust = runs["python"], runs["rust"]
print("same result:", python[0] == rust[0])
print("same order:", python[1] == rust[1])
print("same trace events:", python[2] == rust[2])
