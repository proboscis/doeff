# The error scene: three spawned children, the middle one raising. Gather raises that child's error in the
# parent, so Try around Gather gives Err; the siblings are not cancelled and their writes stay
# (docs/18-effect-combinations.md, Law 9). Prints the order the children ran in, what Try gave, and the store.
from doeff_core_effects import scheduled, state, try_handler

from doeff import Gather, Get, Put, Spawn, Try, do, run


@do
def note(event):
    """Records one step of a child under "trace", so the run can print the order the children ran in."""
    yield Put("trace", (*(yield Get("trace")), event))


@do
def succeeds_with_side_effect(name):
    """A sibling that writes to the store, to show its write stays after another child fails."""
    n = yield Get("side_effects")
    yield Put("side_effects", n + 1)
    yield note(f"{name} wrote side_effects={n + 1}")
    return "ok"


@do
def fails_immediately(name):
    """The child whose error Gather raises in the parent."""
    yield note(f"{name} raises ValueError('boom')")
    raise ValueError("boom")


@do
def parent():
    """Spawns the three children and gathers them under Try, returning what Try gave, the store and the trace."""
    yield Put("side_effects", 0)
    yield Put("trace", ())
    t1 = yield Spawn(succeeds_with_side_effect("t1"))
    t2 = yield Spawn(fails_immediately("t2"))
    t3 = yield Spawn(succeeds_with_side_effect("t3"))
    result = yield Try(Gather(t1, t2, t3))
    return result, (yield Get("side_effects")), (yield Get("trace"))


result, side_effects, trace = run(scheduled(state()(try_handler(parent()))))
for line in trace:
    print("ran:", line)
print("Try(Gather(t1, t2, t3)) ->", result)
print("side_effects ->", side_effects)
