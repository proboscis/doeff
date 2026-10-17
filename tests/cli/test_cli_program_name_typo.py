"""doeff run --program with a misspelt name says the close names the module has.

Before: ``--program myapp.main.programm`` ended at ``Error: module 'myapp.main' has no attribute
'programm'`` and the user had to open the module to find ``program`` (first-errors report
2026-10-17, table row 19, card doeff-publish 8). ``import_symbol`` is the one place every
dotted or ``module:name`` path of ``doeff run`` (--program, --interpreter, --apply, ...) is
resolved, so the close names are added there: the module's own names and, for a package, its
submodules. A name with no close match keeps the plain message.

The test module holds the programs it names (tests/cli/cli_assets.py no longer imports: it calls
``Program.pure``, which ``DoExpr`` does not have, and the CLI reports that as a missing module name).
"""

from __future__ import annotations

import json

import pytest

from doeff import __main__ as cli
from doeff import do

pytestmark = pytest.mark.cli

_HERE = "tests.cli.test_cli_program_name_typo"


@do
def hello():
    return "hello"


hello_program = hello()


def _run_cli(*args: str) -> int:
    return cli.main(["run", "--no-runbox", *args])


def test_dotted_program_name_typo_names_the_close_attribute(capsys) -> None:
    exit_code = _run_cli("--program", f"{_HERE}.hello_programm")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.splitlines() == [
        f"Error: module '{_HERE}' has no attribute 'hello_programm'",
        "  Did you mean 'hello_program'?",
    ]


def test_colon_program_name_typo_names_the_close_attribute(capsys) -> None:
    exit_code = _run_cli("--program", f"{_HERE}:hello_programm")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert f"Error: module '{_HERE}' has no attribute 'hello_programm'" in captured.err
    assert "  Did you mean 'hello_program'?" in captured.err.splitlines()


def test_module_name_typo_inside_a_package_names_the_close_submodule(capsys) -> None:
    exit_code = _run_cli("--program", "tests.cli.test_cli_program_name_typp.hello_program")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Error: module 'tests.cli' has no attribute 'test_cli_program_name_typp'" in captured.err
    assert "  Did you mean 'test_cli_program_name_typo'" in captured.err


def test_interpreter_name_typo_names_the_close_attribute(capsys) -> None:
    exit_code = _run_cli(
        "--program", f"{_HERE}.hello_program",
        "--interpreter", "doeff.cli.run_services.default_interpretr",
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "  Did you mean 'default_interpreter'?" in captured.err.splitlines()


def test_name_with_no_close_match_keeps_the_plain_message(capsys) -> None:
    exit_code = _run_cli("--program", f"{_HERE}.zzzzzz")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.splitlines() == [
        f"Error: module '{_HERE}' has no attribute 'zzzzzz'",
    ]


def test_program_name_typo_is_reported_in_json(capsys) -> None:
    exit_code = _run_cli("--program", f"{_HERE}.hello_programm", "--format", "json")

    captured = capsys.readouterr()
    assert exit_code == 1
    payload = json.loads(captured.out)
    assert payload["status"] == "error"
    assert payload["error"] == "AttributeError"
    assert payload["message"].splitlines() == [
        f"module '{_HERE}' has no attribute 'hello_programm'",
        "  Did you mean 'hello_program'?",
    ]


def test_program_flag_runs_the_name_the_message_suggests(capsys) -> None:
    exit_code = _run_cli("--program", f"{_HERE}.hello_program", "--format", "json")

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert json.loads(captured.out)["result"] == "hello"
