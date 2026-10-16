"""doeff run --program given a @do function itself (not called) says to call it first.

Before: the default interpreter wrapped the function in 7 handlers and failed with
``WithHandler body: `` repeated 7 times before ``expected DoExpr or EffectBase, got:
<class 'function'>`` (first-look report 2026-10-16, card doeff-publish 6 / 2).
``--program`` names a Program instance (docs/14-cli-auto-discovery.md:
``my_program = main()``), so the CLI says how to make one instead of calling the
function itself.
"""

from __future__ import annotations

import json

import pytest

from doeff import __main__ as cli
from doeff import do

pytestmark = pytest.mark.cli

_HERE = "tests.cli.test_cli_rejects_uncalled_do_function"


@do
def hello():
    return "hello"


@do
def greet(name):
    return f"hello {name}"


hello_program = hello()


def test_program_flag_names_the_call_when_given_an_uncalled_do_function(capsys) -> None:
    exit_code = cli.main(["run", "--no-runbox", "--program", f"{_HERE}.hello"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "WithHandler body" not in captured.err
    assert f"--program {_HERE}.hello is the @do function 'hello' itself, not a program" in captured.err
    assert "`hello_program = hello()`" in captured.err
    assert f"`doeff run --program {_HERE}.hello_program`" in captured.err


def test_program_flag_shows_arguments_are_needed_when_the_do_function_has_required_parameters(
    capsys,
) -> None:
    exit_code = cli.main(["run", "--no-runbox", "--program", f"{_HERE}:greet"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "`greet_program = greet(...)`" in captured.err
    assert f"`doeff run --program {_HERE}:greet_program`" in captured.err


def test_program_flag_error_is_reported_in_json(capsys) -> None:
    exit_code = cli.main([
        "run", "--no-runbox", "--program", f"{_HERE}.hello", "--format", "json",
    ])

    captured = capsys.readouterr()
    assert exit_code == 1
    payload = json.loads(captured.out)
    assert payload["status"] == "error"
    assert payload["error"] == "TypeError"
    assert "hello_program = hello()" in payload["message"]


def test_local_runner_names_the_call_when_given_an_uncalled_do_function(capsys) -> None:
    exit_code = cli.main([
        "run", "--no-runbox", "--program", f"{_HERE}.hello",
        "--runner", "doeff.runners.local:run_local",
    ])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "WithHandler body" not in captured.err
    assert f"--program {_HERE}.hello is the @do function 'hello' itself, not a program" in captured.err


def test_program_flag_runs_the_program_the_message_suggests(capsys) -> None:
    exit_code = cli.main([
        "run", "--no-runbox", "--program", f"{_HERE}.hello_program", "--format", "json",
    ])

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert json.loads(captured.out)["result"] == "hello"
