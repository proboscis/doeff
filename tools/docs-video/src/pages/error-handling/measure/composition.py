# The three Try composition guarantees of the page: no rollback, Local restoring env, nested Try results.
from doeff import Ask, Err, Get, Local, Ok, Put, Try, do, run
from doeff_core_effects import local_handler, reader, state, try_handler


@do
def mutate_then_fail():
    yield Put("counter", 1)
    raise ValueError("boom")


@do
def no_rollback():
    yield Put("counter", 0)
    result = yield Try(mutate_then_fail())
    counter = yield Get("counter")
    return (repr(result), counter)


print("no rollback:", run(state()(try_handler(no_rollback()))))


@do
def failing_inner():
    _ = yield Ask("key")
    raise ValueError("fail inside local")


@do
def env_restored():
    before = yield Ask("key")
    caught = yield Try(Local({"key": "inner"}, failing_inner()))
    after = yield Ask("key")
    return (before, isinstance(caught, Err), after)


print("Try(Local(...)):", run(reader(env={"key": "outer"})(try_handler(local_handler(env_restored())))))


@do
def succeeds():
    return 5


@do
def fails():
    raise ValueError("inner failure")


@do
def nested():
    a = yield Try(Try(succeeds()))
    b = yield Try(Try(fails()))
    return (a, b)


print("Try(Try(...)):", run(try_handler(nested())))
