# The scopes scene: Local and Try each wrap a sub-program that changes the environment (Local) and the store
# (Put) and then raises. After the scope, the environment is back to what it was, and the store keeps the write
# (docs/18-effect-combinations.md, Laws 1, 2, 4 and 5). Prints the value of "key" (environment) and "counter"
# (store) before, inside and after each scope.
from doeff_core_effects import local_handler, reader, state, try_handler

from doeff import Ask, Get, Local, Put, Try, do, run


@do
def changes_then_fails():
    _ = yield Ask("key")
    yield Put("counter", 1)
    raise ValueError("boom")


@do
def changes_and_returns():
    inside = yield Ask("key")
    yield Put("counter", 1)
    return inside


@do
def look():
    return (yield Ask("key")), (yield Get("counter"))


@do
def local_then_look():
    yield Put("counter", 0)
    before = yield look()
    inside = yield Local({"key": "inner"}, changes_and_returns())
    after = yield look()
    return before, inside, after


@do
def try_local_then_look():
    yield Put("counter", 0)
    before = yield look()
    caught = yield Try(Local({"key": "inner"}, changes_then_fails()))
    after = yield look()
    return before, caught, after


def stack(program):
    return run(state()(reader(env={"key": "outer"})(try_handler(local_handler(program)))))


before, inside, after = stack(local_then_look())
print("Local, success:   before (key, counter) =", before, " inside key =", repr(inside), " after =", after)
before, caught, after = stack(try_local_then_look())
print("Try(Local), raise: before (key, counter) =", before, " caught =", caught, " after =", after)
