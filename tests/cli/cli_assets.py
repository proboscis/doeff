from __future__ import annotations

from doeff import Ask, Program, default_handlers, do, run
from tests._run_helpers import run_with_defaults

sample_program: Program[int] = Program.pure(5)


@do
def double_program(program: Program[int]) -> int:
    value = yield program
    return value * 2


def add_three(program: Program[int]) -> Program[int]:
    return program.map(lambda value: value + 3)


def sync_interpreter(program: Program[int] | tuple[Program[int], object]) -> int:
    if isinstance(program, tuple):
        program = program[0]
    result = run_with_defaults(program)
    return result.value


@do
def ask_program() -> Program[int]:
    value = yield Ask("value")
    return value


def runresult_interpreter(program: Program[int]):
    return run_with_defaults(program)


def silent_runresult_interpreter(program: Program[int]):
    return run_with_defaults(program)


def ctx_interpreter(program, *, env=None, ctx=None):
    """Interpreter that returns the DoeffRunContext for testing."""
    return ctx


sample_env: Program[dict] = Program.pure({"value": 5})
