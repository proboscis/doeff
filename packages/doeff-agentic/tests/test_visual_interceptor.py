"""Tests for visual logging interceptor wrappers."""

import importlib
from io import StringIO
from pathlib import Path
import re
import sys
import types

import doeff_vm

from doeff import Effect, WithHandler, default_handlers, do, run, slog
from rich.console import Console


_SRC_PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "doeff_agentic"


def _load_visual_modules() -> tuple[types.ModuleType, type]:
    """Import doeff_agentic submodules without executing doeff_agentic/__init__.py."""
    package = types.ModuleType("doeff_agentic")
    package.__path__ = [str(_SRC_PACKAGE_DIR)]
    sys.modules["doeff_agentic"] = package

    effects_module = importlib.import_module("doeff_agentic.effects")
    visual_module = importlib.import_module("doeff_agentic.visual_interceptor")
    return visual_module, effects_module.AgenticSupportsCapability


_visual_module, AgenticSupportsCapability = _load_visual_modules()
VisualInterceptorConfig = _visual_module.VisualInterceptorConfig
visual_logging_console = _visual_module.visual_logging_console
with_visual_logging = _visual_module.with_visual_logging


@do
def _capability_handler(effect: Effect, k):
    if isinstance(effect, AgenticSupportsCapability):
        return (yield doeff_vm.Resume(k, True))
    delegated = yield doeff_vm.Delegate()
    return (yield doeff_vm.Resume(k, delegated))


@do
def _workflow():
    yield slog(status="info", msg="starting")
    supported = yield AgenticSupportsCapability(capability="chat")
    return f"supported={supported}"


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
    result = run(WithHandler(_capability_handler, wrapped), handlers=default_handlers())

    assert result.is_ok()
    assert result.value == "supported=True"

    output = buffer.getvalue()
    assert "starting" in output
    assert "SupportsCapability" in output
    assert "yes" in output


def test_visual_logging_console_wrapper_functions() -> None:
    buffer = StringIO()
    wrapper, _console = visual_logging_console(_make_config(buffer))
    result = run(WithHandler(_capability_handler, wrapper(_workflow())), handlers=default_handlers())

    assert result.is_ok()
    assert result.value == "supported=True"
    assert "SupportsCapability" in buffer.getvalue()


def test_visual_interceptor_source_has_no_intercept_usage() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "doeff_agentic"
        / "visual_interceptor.py"
    ).read_text(encoding="utf-8")

    assert "from doeff import Intercept" not in source
    assert re.search(r"(?<!With)Intercept\s*\(", source) is None
