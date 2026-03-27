"""Integration tests for doeff CLI entry point."""

from __future__ import annotations

import json

import pytest
from doeff import Program, run

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


def test_parse_set_vars_basic() -> None:
    result = cli._parse_set_vars(["key=value", "name=Alice"])
    assert result == {"key": "value", "name": "Alice"}


def test_parse_set_vars_value_with_equals() -> None:
    result = cli._parse_set_vars(["expr=a=b"])
    assert result == {"expr": "a=b"}


def test_parse_set_vars_empty_value() -> None:
    result = cli._parse_set_vars(["key="])
    assert result == {"key": ""}


def test_parse_set_vars_none() -> None:
    result = cli._parse_set_vars(None)
    assert result == {}


def test_parse_set_vars_invalid_format() -> None:
    with pytest.raises(ValueError, match="KEY=VALUE"):
        cli._parse_set_vars(["noequals"])


def test_parse_set_vars_empty_key() -> None:
    with pytest.raises(ValueError, match="empty key"):
        cli._parse_set_vars(["=value"])


_TEST_SYMBOL = {"imported": True}


def _env_interpreter(program, env=None):
    """Interpreter for tests that resolves Ask from env dict."""
    from doeff import WithHandler
    from doeff_core_effects.handlers import (
        reader, state, writer, try_handler, slog_handler,
        local_handler, listen_handler, await_handler,
    )
    from doeff_core_effects.scheduler import scheduled

    env_dict = {}
    if env is not None:
        result = run(env)
        env_dict = result.value if hasattr(result, 'value') else result

    handlers = [
        reader(env_dict), state(), writer(), try_handler, slog_handler(),
        local_handler, listen_handler, await_handler(),
    ]
    wrapped = program
    for h in reversed(handlers):
        wrapped = WithHandler(h, wrapped)
    return run(scheduled(wrapped))


def test_set_vars_injected_into_env(capsys) -> None:
    """Test --set injects values accessible via Ask effect."""
    exit_code = cli.main([
        "run",
        "-c", "from doeff import Ask; v = yield Ask('greeting'); return v",
        "--interpreter", "tests.cli.test_cli_main._env_interpreter",
        "--set", "greeting=hello",
        "--format", "json",
    ])

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    assert payload["result"] == "hello"


def test_set_vars_override_env(capsys) -> None:
    """Test --set overrides values from --env."""
    exit_code = cli.main([
        "run",
        "-c", "from doeff import Ask; v = yield Ask('value'); return v",
        "--interpreter", "tests.cli.test_cli_main._env_interpreter",
        "--set", "value=42",
        "--set", "other=ignored",
        "--format", "json",
    ])

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    assert payload["result"] == "42"


def test_parse_set_vars_import_symbol() -> None:
    result = cli._parse_set_vars(["obj={tests.cli.test_cli_main._TEST_SYMBOL}"])
    assert result == {"obj": {"imported": True}}


def test_parse_set_vars_import_empty_path() -> None:
    with pytest.raises(ValueError, match="empty import path"):
        cli._parse_set_vars(["key={}"])


def test_parse_set_vars_braces_not_closed_is_string() -> None:
    result = cli._parse_set_vars(["key={notclosed"])
    assert result == {"key": "{notclosed"}


def test_set_vars_import_injected_into_env(capsys) -> None:
    """Test --set KEY={symbol} imports and injects the object."""
    exit_code = cli.main([
        "run",
        "-c", "from doeff import Ask; v = yield Ask('obj'); return v",
        "--interpreter", "tests.cli.test_cli_main._env_interpreter",
        "--set", "obj={tests.cli.test_cli_main._TEST_SYMBOL}",
        "--format", "json",
    ])

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    assert payload["result"] == {"imported": True}
