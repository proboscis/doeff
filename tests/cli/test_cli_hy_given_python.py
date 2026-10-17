"""doeff run --hy given Python source says --hy reads Hy and Python goes to -c.

Before: ``--hy 'from myapp.main import program; program'`` ended at ``Error: name 'from' is not
defined`` — Hy's own words for a Python statement, with the step that failed dropped by the
CLI's unwrapping of chained errors (first-errors report 2026-10-17, table row 20, card
doeff-publish 8). ``evaluate_hy_source`` is where the source is read and evaluated, so it adds the
line when the source reads as Python and Hy refused it; the CLI keeps its heading.
"""

from __future__ import annotations

import json
import re
import shlex

import pytest

from doeff import __main__ as cli
from doeff import do

pytest.importorskip("hy")

pytestmark = pytest.mark.cli

_HERE = "tests.cli.test_cli_hy_given_python"
_PYTHON_SOURCE = f"from {_HERE} import hello_program; hello_program"


@do
def hello():
    return "hello"


hello_program = hello()
_HINT_FIRST_LINE = "  --hy reads Hy code, not Python. To run Python code, pass it with -c:"


def _run_cli(*args: str) -> int:
    return cli.main(["run", "--no-runbox", *args])


def test_hy_flag_given_a_python_import_says_python_goes_to_dash_c(capsys) -> None:
    exit_code = _run_cli("--hy", _PYTHON_SOURCE)

    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert exit_code == 1
    assert lines[0] == "Error: Failed to evaluate --hy source: name 'from' is not defined"
    assert _HINT_FIRST_LINE in lines
    assert any(line.startswith("    doeff run -c '") for line in lines)
    assert any(line.startswith("    doeff run --hy '(import ") for line in lines)


def test_hy_flag_given_a_python_call_says_python_goes_to_dash_c(capsys) -> None:
    exit_code = _run_cli("--hy", "Pure(42)")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Error: Failed to evaluate --hy source: name 'Pure' is not defined" in captured.err
    assert _HINT_FIRST_LINE in captured.err.splitlines()


def test_hy_flag_given_python_that_hy_cannot_read_says_python_goes_to_dash_c(capsys) -> None:
    exit_code = _run_cli("--hy", "print('hello')")

    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert exit_code == 1
    assert lines[0].startswith("Error: Failed to parse --hy source: ")
    assert _HINT_FIRST_LINE in lines


def test_hy_flag_python_hint_is_reported_in_json(capsys) -> None:
    exit_code = _run_cli("--hy", _PYTHON_SOURCE, "--format", "json")

    captured = capsys.readouterr()
    assert exit_code == 1
    payload = json.loads(captured.out)
    assert payload["status"] == "error"
    assert payload["error"] == "HyRunnerError"
    message_lines = payload["message"].splitlines()
    assert message_lines[0] == "Failed to evaluate --hy source: name 'from' is not defined"
    assert _HINT_FIRST_LINE in message_lines


def test_hy_flag_misspelt_hy_name_keeps_its_heading_and_gets_no_python_hint(capsys) -> None:
    exit_code = _run_cli("--hy", "(import doeff [Pure]) (Pur 42)")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.splitlines() == [
        "Error: Failed to evaluate --hy source: name 'Pur' is not defined",
    ]


def test_hy_flag_unclosed_hy_form_gets_no_python_hint(capsys) -> None:
    exit_code = _run_cli("--hy", "(import doeff [Pure]")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.startswith("Error: Failed to parse --hy source: ")
    assert "-c" not in captured.err


def _hint_commands(error_text: str) -> list[list[str]]:
    """The ``doeff run …`` example commands the message shows, as argv, with its program swapped for a real one."""
    shown = re.findall(r"^    doeff run (.+)$", error_text, flags=re.MULTILINE)
    return [
        shlex.split(command.replace("myapp.main", _HERE).replace("program", "hello_program"))
        for command in shown
    ]


def test_hy_flag_python_hint_examples_run_when_given_a_real_program(capsys) -> None:
    _run_cli("--hy", _PYTHON_SOURCE)
    commands = _hint_commands(capsys.readouterr().err)
    assert len(commands) == 2

    for command in commands:
        exit_code = _run_cli(*command)
        captured = capsys.readouterr()
        assert exit_code == 0, f"{command}: {captured.err}"
        assert captured.out.strip() == "hello"
