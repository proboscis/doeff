# The async scene: run(scheduled(program)). The scheduler is a handler: it answers Spawn and Gather, and runs the
# tasks cooperatively inside the VM's one synchronous step loop, switching at each wait. Prints the order of the
# steps of two tasks, the result run returns, and the error without scheduled.
from dataclasses import dataclass

from doeff_core_effects import scheduled
from doeff_time import Delay, sim_time_handler

from doeff import EffectBase, Gather, Resume, Spawn, do, handler, run


@dataclass(frozen=True)
class Step(EffectBase):
    text: str


ORDER = []


@handler
def recorded(effect: Step, k):
    ORDER.append(effect.text)
    return (yield Resume(k, None))


@do
def worker(name):
    for n in (1, 2):
        yield Step(f"{name}{n}")
        yield Delay(0)
    return name


@do
def two():
    a = yield Spawn(worker("a"))
    b = yield Spawn(worker("b"))
    return (yield Gather(a, b))


result = run(scheduled(sim_time_handler()(recorded(two()))))
print("run(scheduled(...)):", result, "order", ORDER)
try:
    run(sim_time_handler()(recorded(two())))
except Exception as error:  # noqa: BLE001 - the scene shows the error without the scheduler
    print(f"without scheduled: raises {type(error).__name__}:")
    lines = str(error).strip().splitlines()
    print(lines[0])
    print(next(line.strip() for line in lines if line.strip().startswith("hint:")))
