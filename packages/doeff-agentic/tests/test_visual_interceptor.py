"""Tests for visual logging interceptor wrappers."""

from io import StringIO

from doeff_agentic.effects import AgenticSupportsCapability
from doeff_agentic.visual_interceptor import (
    VisualInterceptorConfig,
    visual_logging_console,
    with_visual_logging,
)
from doeff_core_effects import slog_handler, state
from rich.console import Console

from doeff import Effect, Pass, Resume, do, run, slog
from doeff import handler as _install_raw_handler


@do
def _capability_handler(effect: Effect, k):
    if isinstance(effect, AgenticSupportsCapability):
        return (yield Resume(k, True))
    yield Pass(effect, k)
    return None


@do
def _workflow():
    yield slog(status="info", msg="starting")
    supported = yield AgenticSupportsCapability(capability="chat")
    return f"supported={supported}"


def _run(program):
    # slog_handler keeps its log via Get/Put, so state sits outside it.
    return run(state()(slog_handler(_install_raw_handler(_capability_handler)(program))))


def _make_config(buffer: StringIO) -> VisualInterceptorConfig:
    return VisualInterceptorConfig(
        show_timestamps=False,
        show_duration=False,
        show_slog=True,
        console=Console(file=buffer, force_terminal=False, color_system=None),
    )


def test_with_visual_logging_logs_and_preserves_result() -> None:
    buffer = StringIO()
    wrapped = with_visual_logging(_workflow(), _make_config(buffer))
    result = _run(wrapped)

    assert result == "supported=True"

    output = buffer.getvalue()
    assert "starting" in output
    assert "SupportsCapability" in output
    assert "yes" in output


def test_visual_logging_console_wrapper_functions() -> None:
    buffer = StringIO()
    wrapper, _console = visual_logging_console(_make_config(buffer))
    result = _run(wrapper(_workflow()))

    assert result == "supported=True"
    assert "SupportsCapability" in buffer.getvalue()
