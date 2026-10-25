# The observation scene: WithObserve(observer, body) calls the observer with each effect dispatched in its body.
# It wraps the program like a handler; run takes no trace parameter. Prints the answer with and without the
# observer (the same), and what the observer saw. The observer sees only its body: Tell("start"), yielded
# before the observed part, is not in the list.
from doeff_core_effects import Ask, Get, Put, Tell
from doeff_core_effects.handlers import reader, state, writer

from doeff import WithObserve, do, run

seen = []


@do
def bump():
    key = yield Ask("key")
    current = yield Get(key)
    yield Put(key, current + 1)
    yield Tell(f"{key} is {current + 1}")
    return current + 1


@do
def plain():
    yield Tell("start")
    return (yield bump())


@do
def watched():
    yield Tell("start")
    return (yield WithObserve(seen.append, bump()))


def stack(program):
    return state(initial={"count": 41})(writer(reader(env={"key": "count"})(program)))


print("plain():", run(stack(plain())))
print("watched():", run(stack(watched())))
for effect in seen:
    print("  seen:", repr(effect))
