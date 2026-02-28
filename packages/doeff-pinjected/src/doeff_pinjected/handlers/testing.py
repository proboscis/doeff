"""Testing handlers for doeff-pinjected effects."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from doeff import Effect, Pass, Resume, do
from doeff_pinjected.effects import PinjectedProvide, PinjectedResolve

ProtocolHandler = Callable[[Any, Any], Any]


@dataclass
class MockPinjectedRuntime:
    """In-memory runtime used by mock pinjected handlers."""

    bindings: dict[Any, Any] = field(default_factory=dict)
    resolve_calls: list[Any] = field(default_factory=list)
    provide_calls: list[tuple[Any, Any]] = field(default_factory=list)

    @classmethod
    def from_bindings(
        cls,
        *,
        bindings: Mapping[Any, Any] | None = None,
    ) -> MockPinjectedRuntime:
        runtime = cls()
        if bindings:
            runtime.bindings.update(dict(bindings))
        return runtime


def mock_handlers(
    *,
    bindings: Mapping[Any, Any] | None = None,
    runtime: MockPinjectedRuntime | None = None,
) -> ProtocolHandler:
    """Build deterministic in-memory protocol handler for pinjected bridge effects."""

    active_runtime = runtime or MockPinjectedRuntime.from_bindings(bindings=bindings)
    if runtime is not None and bindings:
        active_runtime.bindings.update(dict(bindings))

    @do
    def handler(effect: Effect, k: Any):
        if isinstance(effect, PinjectedResolve):
            active_runtime.resolve_calls.append(effect.key)
            if effect.key not in active_runtime.bindings:
                raise KeyError(effect.key)
            return (yield Resume(k, active_runtime.bindings[effect.key]))
        if isinstance(effect, PinjectedProvide):
            active_runtime.provide_calls.append((effect.key, effect.value))
            active_runtime.bindings[effect.key] = effect.value
            return (yield Resume(k, None))
        return (yield Pass())

    return handler


__all__ = [
    "MockPinjectedRuntime",
    "ProtocolHandler",
    "mock_handlers",
]
