from __future__ import annotations

import doeff_vm

from doeff import Apply, Ask, Effect, Get, KleisliProgram, Program, Pure, default_handlers, do, run
from doeff import ProgramBase
from tests._run_helpers import run_with_defaults


def _meta(fn):
    code = fn.__code__
    return {
        "function_name": code.co_name,
        "source_file": code.co_filename,
        "source_line": code.co_firstlineno,
    }


def test_run_resolves_plain_value_arg_before_kernel_call() -> None:
    @do
    def add_one(x: int):
        return x + 1

    result = run_with_defaults(add_one(41))
    assert result.value == 42










