# The call stack scene: a program calls programs. Each call is a frame in the VM with its function, file and
# line, so when the innermost one raises, the doeff traceback lists the chain of @do calls and the line each one
# is at. Prints the doeff traceback of the error.
from doeff_core_effects import Ask
from doeff_core_effects import reader

from doeff import do, run
from doeff.traceback import format_default


@do
def checkout():
    paid = yield pay(120)
    return paid


@do
def pay(amount):
    return (yield charge(amount))


@do
def charge(amount):
    limit = yield Ask("limit")
    if amount > limit:
        raise ValueError(f"{amount} > {limit}")
    return amount


try:
    run(reader(env={"limit": 100})(checkout()))
except ValueError as error:
    print(format_default(error))
