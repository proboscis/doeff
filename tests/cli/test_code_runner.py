from __future__ import annotations

import pytest

from doeff import Program
from doeff.cli.code_runner import (
    TransformResult,
    execute_doeff_code,
    transform_doeff_code,
)


class TestTransformDoeffCode:
    def test_simple_expression_without_yield(self) -> None:
        source = "1 + 2"
        result = transform_doeff_code(source)

        assert isinstance(result, TransformResult)
        assert result.has_yield is False
        assert result.original_source == source

    def test_import_and_expression(self) -> None:
        source = """
from doeff import Program
Program.pure(42)
"""
        result = transform_doeff_code(source)
        assert result.has_yield is False

    def test_code_with_toplevel_yield(self) -> None:
        source = """
x = yield some_effect()
x + 1
"""
        result = transform_doeff_code(source)
        assert result.has_yield is True

    def test_yield_inside_function_not_detected_as_toplevel(self) -> None:
        source = """
def my_gen():
    yield 1
    yield 2

my_gen()
"""
        result = transform_doeff_code(source)
        assert result.has_yield is False

    def test_syntax_error_raises(self) -> None:
        source = "def broken("
        with pytest.raises(SyntaxError):
            transform_doeff_code(source)


class TestExecuteDoeffCode:
    def test_simple_arithmetic(self) -> None:
        result = execute_doeff_code("1 + 2 + 3")
        assert result == 6

    def test_program_pure(self) -> None:
        result = execute_doeff_code("from doeff import Program; Program.pure(42)")
        assert isinstance(result, Program)

    def test_multiple_statements_last_is_result(self) -> None:
        source = """
x = 10
y = 20
x + y
"""
        result = execute_doeff_code(source)
        assert result == 30

    def test_import_preserved(self) -> None:
        source = """
import json
json.dumps({"key": "value"})
"""
        result = execute_doeff_code(source)
        assert result == '{"key": "value"}'

    def test_code_with_yield_creates_program(self) -> None:
        source = """
from doeff import Program
value = yield Program.pure(10)
value * 2
"""
        result = execute_doeff_code(source)
        assert isinstance(result, Program)

    def test_extra_globals_injected(self) -> None:
        result = execute_doeff_code(
            "my_value * 2",
            extra_globals={"my_value": 21},
        )
        assert result == 42

    def test_empty_code_returns_none(self) -> None:
        result = execute_doeff_code("")
        assert result is None

    def test_code_with_only_statements_no_expression(self) -> None:
        source = """
x = 1
y = 2
"""
        result = execute_doeff_code(source)
        assert result is None

    def test_heredoc_style_multiline(self) -> None:
        source = """from doeff import Program
from doeff.effects import Ask

issue_id = yield Ask("ISSUE_ID")
Program.pure(f"Processing {issue_id}")
"""
        result = execute_doeff_code(source)
        assert isinstance(result, Program)


class TestLineNumberPreservation:
    def test_error_shows_original_line_number(self) -> None:
        source = """x = 1
y = 2
raise ValueError("error on line 3")
"""
        with pytest.raises(ValueError) as exc_info:
            exec_globals: dict = {"__builtins__": __builtins__}
            result = transform_doeff_code(source, filename="test.py")
            exec(result.code, exec_globals)

        assert "error on line 3" in str(exc_info.value)
