from __future__ import annotations

import pytest

from doeff import Program, ProgramInterpreter
from doeff.cli.code_runner import (
    TransformResult,
    execute_doeff_code,
    transform_doeff_code,
)


def run_program(program: Program):
    return ProgramInterpreter().run(program).value


class TestTransformDoeffCode:
    def test_returns_transform_result(self) -> None:
        source = "1 + 2"
        result = transform_doeff_code(source)

        assert isinstance(result, TransformResult)
        assert result.original_source == source

    def test_syntax_error_raises(self) -> None:
        source = "def broken("
        with pytest.raises(SyntaxError):
            transform_doeff_code(source)


class TestExecuteDoeffCode:
    def test_simple_arithmetic(self) -> None:
        result = execute_doeff_code("1 + 2 + 3")
        assert isinstance(result, Program)
        assert run_program(result) == 6

    def test_program_pure(self) -> None:
        result = execute_doeff_code("from doeff import Program; Program.pure(42)")
        assert isinstance(result, Program)
        assert run_program(result) == 42

    def test_multiple_statements_last_is_result(self) -> None:
        source = """
x = 10
y = 20
x + y
"""
        result = execute_doeff_code(source)
        assert isinstance(result, Program)
        assert run_program(result) == 30

    def test_import_preserved(self) -> None:
        source = """
import json
json.dumps({"key": "value"})
"""
        result = execute_doeff_code(source)
        assert isinstance(result, Program)
        assert run_program(result) == '{"key": "value"}'

    def test_code_with_yield_creates_program(self) -> None:
        source = """
from doeff import Program
value = yield Program.pure(10)
value * 2
"""
        result = execute_doeff_code(source)
        assert isinstance(result, Program)
        assert run_program(result) == 20

    def test_extra_globals_injected(self) -> None:
        result = execute_doeff_code(
            "my_value * 2",
            extra_globals={"my_value": 21},
        )
        assert isinstance(result, Program)
        assert run_program(result) == 42

    def test_empty_code_returns_program(self) -> None:
        result = execute_doeff_code("")
        assert isinstance(result, Program)

    def test_code_with_only_statements(self) -> None:
        source = """
x = 1
y = 2
"""
        result = execute_doeff_code(source)
        assert isinstance(result, Program)

    def test_heredoc_style_multiline(self) -> None:
        source = """from doeff import Program
from doeff.effects import Ask

issue_id = yield Ask("ISSUE_ID")
Program.pure(f"Processing {issue_id}")
"""
        result = execute_doeff_code(source)
        assert isinstance(result, Program)


class TestLineNumberPreservation:
    def test_line_numbers_preserved_in_compiled_code(self) -> None:
        source = """x = 1
y = 2
z = 3
"""
        result = transform_doeff_code(source, filename="custom_file.py")
        assert result.code.co_filename == "custom_file.py"
        assert result.original_source == source
