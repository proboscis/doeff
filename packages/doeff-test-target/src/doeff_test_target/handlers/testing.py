"""In-memory mock handlers for doeff-test-target fixture effects."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from doeff import Pass, Resume
from doeff_test_target.effects import ReadFixtureValue, RecordFixtureEvent

ProtocolHandler = Callable[[Any, Any], Any]


@dataclass
class MockFixtureRuntime:
    """In-memory runtime for doeff-test-target fixture handlers."""

    seed_env: dict[str, Any] = field(default_factory=dict)
    emitted_events: list[str] = field(default_factory=list)
    default_prefix: str = "mock-"

    def resolve(self, key: str) -> Any:
        return self.seed_env.get(key, f"{self.default_prefix}{key}")

    def handle_read_value(self, effect: ReadFixtureValue, k):
        return (yield Resume(k, self.resolve(effect.key)))

    def handle_record_event(self, effect: RecordFixtureEvent, k):
        self.emitted_events.append(effect.message)
        return (yield Resume(k, None))


def mock_handlers(
    *,
    seed_env: Mapping[str, Any] | None = None,
    default_prefix: str = "mock-",
    runtime: MockFixtureRuntime | None = None,
) -> ProtocolHandler:
    """Build a mock protocol handler for doeff-test-target fixture effects."""

    active_runtime = runtime or MockFixtureRuntime(
        seed_env=dict(seed_env or {}),
        default_prefix=default_prefix,
    )

    def handler(effect: Any, k: Any):
        if isinstance(effect, ReadFixtureValue):
            return (yield from active_runtime.handle_read_value(effect, k))
        if isinstance(effect, RecordFixtureEvent):
            return (yield from active_runtime.handle_record_event(effect, k))
        yield Pass()

    return handler


__all__ = [
    "MockFixtureRuntime",
    "ProtocolHandler",
    "mock_handlers",
]
