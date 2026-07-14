"""Regression tests for lazy source-line lookup in ``run()`` tracebacks."""

import linecache
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

import doeff.traceback as doeff_traceback
from doeff import EffectGenerator, do, run


def test_run_defers_source_line_lookup_until_traceback_rendering(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Trace extraction stays lazy while rendering still includes source text."""
    expected_exception: ValueError = ValueError("lazy traceback line lookup")

    @do
    def failing_program() -> EffectGenerator[None]:
        if False:
            yield None
        raise expected_exception  # rendered-source-line-sentinel

    line_lookups: list[tuple[str, int]] = []
    line_lookups_at_format_start: list[tuple[str, int]] = []
    original_getline: Callable[..., str] = linecache.getline
    original_format_default: Callable[[BaseException], str | None] = (
        doeff_traceback.format_default
    )

    def recording_getline(
        filename: str,
        lineno: int,
        module_globals: dict[str, Any] | None = None,
    ) -> str:
        line_lookups.append((filename, lineno))
        return original_getline(filename, lineno, module_globals)

    def recording_format_default(exception: BaseException) -> str | None:
        line_lookups_at_format_start.extend(line_lookups)
        rendered: str | None = original_format_default(exception)
        return rendered

    monkeypatch.setattr(linecache, "getline", recording_getline)
    monkeypatch.setattr(doeff_traceback, "format_default", recording_format_default)

    with pytest.raises(ValueError, match="lazy traceback line lookup") as exc_info:
        run(failing_program())

    assert exc_info.value is expected_exception
    assert line_lookups_at_format_start == []
    assert line_lookups
    assert "raise expected_exception  # rendered-source-line-sentinel" in capsys.readouterr().err


def test_source_line_lookup_warns_when_linecache_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source lookup degradation remains non-fatal but is never silent."""

    def failing_getline(filename: str, lineno: int) -> str:
        raise OSError(f"cannot read {filename}:{lineno}")

    monkeypatch.setattr(doeff_traceback, "linecache", SimpleNamespace(getline=failing_getline))
    exception = ValueError("traceback source unavailable")
    exception.__doeff_traceback__ = [
        ["frame", "failing_program", "/unavailable/program.py", 17]
    ]

    with pytest.warns(RuntimeWarning, match=r"/unavailable/program\.py:17"):
        rendered = doeff_traceback.format_default(exception)

    assert rendered is not None
    assert "failing_program()" in rendered
    assert "ValueError: traceback source unavailable" in rendered
