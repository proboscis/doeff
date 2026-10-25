# Two tasks each perform three steps. The scheduler runs one task at a time on one thread: a task keeps running
# until it waits, then the scheduler resumes the next ready one. Prints the order of the steps twice: once with
# tasks that never wait, once with a Delay(0) after each step. Backs the scheduling-model scene of the Async
# Effects video.
from dataclasses import dataclass

from doeff_core_effects.scheduler import scheduled
from doeff_time import Delay, sim_time_handler

from doeff import EffectBase, Gather, Resume, Spawn, do, handler, run


@dataclass(frozen=True)
class Step(EffectBase):
    text: str


ORDER = []


@handler
def recorded(effect: Step, k):  # keeps the order the steps are performed in
    ORDER.append(effect.text)
    return (yield Resume(k, None))


@do
def busy(name):  # never waits
    for n in (1, 2, 3):
        yield Step(f"{name}{n}")
    return name


@do
def polite(name):  # waits after each step
    for n in (1, 2, 3):
        yield Step(f"{name}{n}")
        yield Delay(0)
    return name


@do
def two(worker):
    a = yield Spawn(worker("a"))
    b = yield Spawn(worker("b"))
    return (yield Gather(a, b))


for i in range(3):
    for worker in (busy, polite):
        ORDER.clear()
        result = run(scheduled(sim_time_handler()(recorded(two(worker)))))
        print(f"run {i + 1} {worker.__name__}: result {result} order {ORDER}")
