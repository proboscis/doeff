# WithObserve(observer, body) calls the observer with each effect yielded inside body, including the effects of
# tasks Spawned inside it, and only those. The observer only looks: what it returns is ignored, and the effect
# still goes on to its handler, which answers it as before. Prints what the observer saw and what the program got,
# to back the WithObserve scene of the Advanced Effects video (the example of docs/09-advanced-effects.md).
from doeff_core_effects import Ask, ReadWriterLog, Tell, reader, scheduled, state, writer

from doeff import Gather, Spawn, WithObserve, do, run


def name_of(effect):
    kind = type(effect).__name__
    for field in ("key", "msg"):
        if hasattr(effect, field):
            return f"{kind}({getattr(effect, field)!r})"
    return kind


def observer(effect):
    print("   observer saw", name_of(effect))
    return "ignored"  # the observer's value is not used


@do
def child(n):
    user = yield Ask("user")
    yield Tell(f"child {n} served {user}")
    return f"{user}-{n}"


@do
def body():
    t1 = yield Spawn(child(1))
    t2 = yield Spawn(child(2))
    return (yield Gather(t1, t2))


@do
def program():
    yield Tell("before the scope")
    result = yield WithObserve(observer, body())
    yield Tell("after the scope")
    return result, (yield ReadWriterLog())


for i in range(3):
    print(f"run {i + 1}:")
    result, log = run(state()(scheduled(reader(env={"user": "ada"})(writer(program())))))
    print(f"   result {result!r}")
    print(f"   log {log!r}")
