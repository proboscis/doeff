# The observe scene: WithObserve around a body that spawns two children, each yielding Ask. The observer is
# called for every effect in the body, the children's included, and its return value is ignored: the children
# still get the reader's answer (docs/18-effect-combinations.md, Law 6). Prints each effect the observer saw, in
# order, and what the body returned.
from doeff_core_effects import scheduled
from doeff_core_effects.handlers import reader

from doeff import Ask, AskEffect, Gather, Spawn, WithObserve, do, run


@do
def child():
    """A spawned child that asks for "key", to show the observer sees effects of spawned tasks too."""
    return (yield Ask("key"))


@do
def body():
    """The observed body: spawns two children and gathers their answers."""
    t1 = yield Spawn(child())
    t2 = yield Spawn(child())
    return (yield Gather(t1, t2))


seen = []


def name_of(effect):
    """The effect as the video lists it: its type, and the key of an Ask."""
    return type(effect).__name__ + (f"({effect.key!r})" if isinstance(effect, AskEffect) else "")


def observer(effect):
    """Keeps the name of every effect it is shown, and tries to answer: its return value goes nowhere."""
    seen.append(name_of(effect))
    return "ignored"


@do
def observed():
    """WithObserve around the body, as Law 6 of the page writes it."""
    return (yield WithObserve(observer, body()))


result = run(scheduled(reader({"key": "value"})(observed())))
for name in seen:
    print("observer saw:", name)
print("asks seen:", [name for name in seen if name.startswith("Ask")])
print("body returned:", result)
