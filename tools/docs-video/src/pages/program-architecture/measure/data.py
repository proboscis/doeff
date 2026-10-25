# The data scene: an effect is a plain value, and calling a @do function builds a program without running it.
# Prints the effect and the program as values, that the body has not run yet, and that `yield Ask(...)` and
# `yield Perform(Ask(...))` get the same answer from the same handler: dispatch happens only at Perform, and the
# VM wraps a bare effect in it. run returns the raw value.
from doeff_core_effects import Ask
from doeff_core_effects.handlers import reader

from doeff import DoExpr, Perform, do, run

effect = Ask("key")
print("effect:", repr(effect))

RAN = []


@do
def short():
    RAN.append("short")
    value = yield Ask("key")
    return value


@do
def lowered():
    RAN.append("lowered")
    value = yield Perform(Ask("key"))
    return value


program = short()
print("program is a DoExpr:", isinstance(program, DoExpr))
print("body ran on call:", RAN)
env = reader(env={"key": "ada"})
print("run(yield Ask):", repr(run(env(program))))
print("run(yield Perform(Ask)):", repr(run(env(lowered()))))
print("bodies ran:", RAN)
