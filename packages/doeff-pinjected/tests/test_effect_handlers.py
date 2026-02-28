"""Tests for doeff-pinjected effect and handler modules."""


import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from doeff_pinjected.effects import PinjectedProvide, PinjectedResolve  # noqa: E402
from doeff_pinjected.handlers import (  # noqa: E402
    MockPinjectedRuntime,
    mock_handlers,
    production_handlers,
)

from doeff import (  # noqa: E402
    WithHandler,
    async_run,
    default_async_handlers,
    default_handlers,
    do,
    run,
)


def _is_ok(run_result: Any) -> bool:
    checker = run_result.is_ok
    return bool(checker()) if callable(checker) else bool(checker)


class FakeAsyncResolver:
    """Resolver stub used to emulate AsyncResolver.provide in tests."""

    def __init__(self, bindings: dict[Any, Any]) -> None:
        self.bindings = dict(bindings)
        self.calls: list[Any] = []

    async def provide(self, key: Any) -> Any:
        self.calls.append(key)
        if key not in self.bindings:
            raise KeyError(key)
        return self.bindings[key]


def _run_with_handler(program, handler):
    return run(
        WithHandler(handler, program),
        handlers=default_handlers(),
    )


async def _async_run_with_handler(program, handler):
    return await async_run(
        WithHandler(handler, program),
        handlers=default_async_handlers(),
    )


def test_effect_exports() -> None:
    exported_effects = importlib.import_module("doeff_pinjected.effects")
    assert exported_effects.PinjectedResolve is PinjectedResolve
    assert exported_effects.PinjectedProvide is PinjectedProvide


def test_handler_exports() -> None:
    exported_handlers = importlib.import_module("doeff_pinjected.handlers")
    assert exported_handlers.production_handlers is production_handlers
    assert exported_handlers.mock_handlers is mock_handlers


def test_top_level_module_keeps_legacy_bridge_exports() -> None:
    module = importlib.import_module("doeff_pinjected")
    assert "program_to_injected" in module.__all__
    assert "program_to_iproxy" in module.__all__


def test_mock_handlers_support_resolve_and_provide() -> None:
    runtime = MockPinjectedRuntime.from_bindings(bindings={"service": "mock-v1"})

    @do
    def workflow():
        first = yield PinjectedResolve(key="service")
        _ = yield PinjectedProvide(key="service", value="mock-v2")
        second = yield PinjectedResolve(key="service")
        return first, second

    result = _run_with_handler(workflow(), mock_handlers(runtime=runtime))

    assert _is_ok(result)
    assert result.value == ("mock-v1", "mock-v2")
    assert runtime.resolve_calls == ["service", "service"]
    assert runtime.provide_calls == [("service", "mock-v2")]


@do
def _resolve_service():
    return (yield PinjectedResolve(key="service"))


@do
def _resolve_and_override_service():
    first = yield PinjectedResolve(key="service")
    _ = yield PinjectedProvide(key="service", value="prod-override")
    second = yield PinjectedResolve(key="service")
    return first, second


@pytest.mark.asyncio
async def test_handler_swapping_between_mock_and_production() -> None:
    resolver = FakeAsyncResolver({"service": "prod-v1"})

    mock_result = _run_with_handler(
        _resolve_service(),
        mock_handlers(bindings={"service": "mock-v1"}),
    )
    production_result = await _async_run_with_handler(
        _resolve_and_override_service(),
        production_handlers(resolver=resolver),
    )

    assert _is_ok(mock_result)
    assert _is_ok(production_result)
    assert mock_result.value == "mock-v1"
    assert production_result.value == ("prod-v1", "prod-override")
    assert resolver.calls == ["service"]
