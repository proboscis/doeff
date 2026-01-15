from __future__ import annotations

from doeff import Program, CESKInterpreter, do
from doeff.effects import Ask

sample_program: Program[int] = Program.pure(5)


@do
def double_program(program: Program[int]) -> int:
    value = yield program
    return value * 2


def add_three(program: Program[int]) -> Program[int]:
    return program.map(lambda value: value + 3)


def sync_interpreter(program: Program[int]) -> int:
    interpreter = CESKInterpreter()
    result = interpreter.run(program)
    return result.value


@do
def ask_program() -> Program[int]:
    value = yield Ask("value")
    return value


def runresult_interpreter(program: Program[int]):
    interpreter = CESKInterpreter()
    return interpreter.run(program)


sample_env: Program[dict] = Program.pure({"value": 5})
