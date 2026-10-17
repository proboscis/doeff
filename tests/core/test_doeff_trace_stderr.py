from __future__ import annotations

import pytest

from doeff import (
    Ask,
    Program,
    do,
)
from doeff.traceback import format_default
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


def test_no_heading_when_no_frame_is_left_to_show() -> None:
    # An error raised inside doeff's own code carries only frames the rendering skips: a heading
    # with nothing under it hid the real error among empty lines (first-look error table
    # 2026-10-17, row 14).
    error = RuntimeError("raised inside the scheduler")
    error.__doeff_traceback__ = [
        ["frame", "handle_scheduler_effect", "/site-packages/doeff_core_effects/scheduler.py", 1],
    ]
    assert format_default(error) is None


def test_run_no_stderr_on_success(capsys: pytest.CaptureFixture[str]) -> None:
    @do
    def ok() -> Program[int]:
        return 42

    result = run_with_defaults(ok())
    assert result.is_ok()

    captured = capsys.readouterr()
    assert captured.err == ""
