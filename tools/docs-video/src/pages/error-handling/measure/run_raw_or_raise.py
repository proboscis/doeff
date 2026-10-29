# run() hands back the program's value as it is, or re-raises the program's exception: no wrapper either way.
# Backs the opening scene of the error-handling video: the same program, run with a name and with an empty one.
from doeff import Ask, do, run
from doeff_core_effects import reader


@do
def greet_or_fail():
    name = yield Ask("name")
    if not name:
        raise ValueError("name is empty")
    return name.upper()


print("run ->", repr(run(reader(env={"name": "doeff"})(greet_or_fail()))))
try:
    run(reader(env={"name": ""})(greet_or_fail()))
except ValueError as error:
    print("run raised", repr(error))
