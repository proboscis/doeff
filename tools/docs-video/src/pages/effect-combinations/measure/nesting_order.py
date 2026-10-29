# The nesting scene: the same two effects, Try and Listen, nested both ways around a program that tells and then
# raises. The outer one decides what the caller gets (docs/18-effect-combinations.md, Law 3 and the matrix's
# Listen / Try cells). Prints what each nesting returns, and Listen's capture when the program succeeds.
from doeff_core_effects import listen_handler, state, try_handler, writer

from doeff import Listen, Tell, Try, do, run


@do
def tells_then_fails():
    yield Tell("loaded config")
    yield Tell("parsing rows")
    raise ValueError("bad row")


@do
def tells_then_succeeds():
    yield Tell("loaded config")
    yield Tell("parsing rows")
    return 42


def logs(entries):
    return [entry.msg for entry in entries]


@do
def try_outside():
    return (yield Try(Listen(tells_then_fails())))


@do
def listen_outside():
    caught, entries = yield Listen(Try(tells_then_fails()))
    return caught, logs(entries)


@do
def listen_success():
    value, entries = yield Listen(tells_then_succeeds())
    return value, logs(entries)


def stack(program):
    return run(state()(writer(listen_handler(try_handler(program)))))


print("Try(Listen(p)):", stack(try_outside()))
print("Listen(Try(p)):", stack(listen_outside()))
print("Listen(ok):    ", stack(listen_success()))
