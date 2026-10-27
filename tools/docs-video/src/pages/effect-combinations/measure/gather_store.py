# The scheduler scene: three spawned children each read "counter", maybe suspend, and write counter + 1. The
# scheduler runs one task at a time and switches only where a task suspends, so the children that do not
# suspend each see the previous write, and the children that suspend on Delay between the read and the write all
# read the same old value (docs/18-effect-combinations.md, Laws 8a and 8b). Runs both under the Rust and the
# Python scheduler and prints, for each, the order of the reads and writes and what Gather and the store hold.
# The suspension is the page's own: Delay(0.05) on sim_time_handler()'s virtual clock, the example of Law 8b.
from doeff_core_effects import scheduled
from doeff_core_effects.handlers import state
from doeff_time import Delay, sim_time_handler

from doeff import Gather, Get, Put, Spawn, do, run


@do
def note(event):
    """Records one step of a child under "trace", so the run can print the order the scheduler ran them in."""
    yield Put("trace", (*(yield Get("trace")), event))


def increment(name, suspend):
    """The child of the page's Laws 8a / 8b: a read-modify-write of "counter", suspending in between or not."""

    @do
    def child():
        n = yield Get("counter")
        yield note(f"{name} read {n}")
        if suspend:
            yield note(f"{name} suspends")
            yield Delay(0.05)
        yield Put("counter", n + 1)
        yield note(f"{name} wrote {n + 1}")
        return n

    return child()


def gather_three(suspend):
    """Spawns three such children and gathers them, returning Gather's list, the counter and the trace."""

    @do
    def parent():
        yield Put("counter", 0)
        yield Put("trace", ())
        t1 = yield Spawn(increment("t1", suspend))
        t2 = yield Spawn(increment("t2", suspend))
        t3 = yield Spawn(increment("t3", suspend))
        results = yield Gather(t1, t2, t3)
        return results, (yield Get("counter")), (yield Get("trace"))

    return parent()


for implementation in ("rust", "python"):
    for suspend in (False, True):
        results, counter, trace = run(scheduled(sim_time_handler()(state()(gather_three(suspend))), implementation=implementation))
        label = "suspends between read and write" if suspend else "does not suspend"
        print(f"[{implementation}] child {label}:")
        print("  order:", ", ".join(trace))
        print("  Gather ->", results, " counter ->", counter)
