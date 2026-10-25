# The arguments scene: a @do function's arguments are passed as given (docs/11-kleisli-arrows.md, "Arguments Are
# Passed As Given" and "Resolving in the Caller"). An effect or a program passed in stays a value; the body yields
# it to get its result, or the caller yields first and passes a plain value.
from doeff_core_effects.handlers import reader

from doeff import Ask, Pure, do, run


@do
def echo(x: int):
    return x


@do
def use_values(x, raw_program):
    x_value = yield x
    y = yield raw_program
    return f"{x_value}:{y}"


@do
def add_one(x: int):
    return x + 1


@do
def caller():
    x = yield Ask("x")
    return (yield add_one(x))


with_env = reader(env={"x": 5})
call_expr = use_values(Ask("x"), Pure(10))
print("use_values(Ask('x'), Pure(10)).args:", call_expr.args)
print("run(with_env(echo(Ask('x')))):", repr(run(with_env(echo(Ask("x"))))))
print("run(with_env(use_values(Ask('x'), Pure(10)))):", repr(run(with_env(call_expr))))
print("run(with_env(caller())):", repr(run(with_env(caller()))))
