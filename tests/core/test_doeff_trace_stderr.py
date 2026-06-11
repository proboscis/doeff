from __future__ import annotations

import pytest

from doeff import (
    Ask,
    Program,
    do,
)
from tests._run_helpers import run_with_defaults


def test_run_prints_trace_when_flag_is_true(capsys: pytest.CaptureFixture[str]) -> None:
    @do
    def failing() -> Program[int]:
        _ = yield Ask("nonexistent_key")
        return 42

    result = run_with_defaults(failing())
    assert result.is_err()

    captured = capsys.readouterr()
    assert "doeff Traceback" in captured.err
    assert "nonexistent_key" in captured.err




def test_run_no_stderr_on_success(capsys: pytest.CaptureFixture[str]) -> None:
    @do
    def ok() -> Program[int]:
        return 42

    result = run_with_defaults(ok())
    assert result.is_ok()

    captured = capsys.readouterr()
    assert captured.err == ""
