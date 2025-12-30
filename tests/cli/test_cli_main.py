"""Integration tests for doeff CLI entry point."""

from __future__ import annotations

import json

import pytest

from doeff import __main__ as cli


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
