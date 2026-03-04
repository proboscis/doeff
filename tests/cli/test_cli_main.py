"""Integration tests for doeff CLI entry point."""

from __future__ import annotations

import json

import pytest
from doeff import Program, default_handlers, run

from doeff import __main__ as cli

pytestmark = pytest.mark.cli


@pytest.mark.usefixtures("capsys")
def test_main_json_error_includes_traceback(capsys):
    exit_code = cli.main(
        [
            "run",
            "--program",
            "nonexistent.module.program",
            "--interpreter",
            "doeff.program.Program",
            "--format",
            "json",
        ]
    )

    assert exit_code == 1

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["status"] == "error"
    assert payload["error"] == "ModuleNotFoundError"
    assert "traceback" in payload
    assert "ModuleNotFoundError" in payload["traceback"]
    assert "nonexistent" in payload["traceback"]


class _DuckRunResultLike:
    def __init__(self, value: int) -> None:
        self.value = value

    def is_ok(self) -> bool:
        return True


def test_finalize_result_unwraps_real_run_result() -> None:
    run_result = run(Program.pure(7), handlers=default_handlers())

    final_value, finalized_run_result = cli._finalize_result(run_result)

    assert final_value == 7
    assert finalized_run_result is run_result


def test_finalize_result_does_not_duck_type_run_result_like_object() -> None:
    duck_value = _DuckRunResultLike(42)

    final_value, finalized_run_result = cli._finalize_result(duck_value)

    assert final_value is duck_value
    assert finalized_run_result is None
