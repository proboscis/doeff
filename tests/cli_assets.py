from __future__ import annotations

from doeff import Program, ProgramInterpreter, do

sample_program: Program[int] = Program.pure(5)


@do
def double_program(program: Program[int]) -> int:
    value = yield program
    return value * 2


def add_three(program: Program[int]) -> Program[int]:
    return program.map(lambda value: value + 3)


def sync_interpreter(program: Program[int]) -> int:
    interpreter = ProgramInterpreter()
    result = interpreter.run(program)
    return result.value
