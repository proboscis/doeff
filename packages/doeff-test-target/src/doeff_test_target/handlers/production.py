"""Production handlers for doeff-test-target fixture effects."""


from collections.abc import Callable, Mapping, MutableSequence
from dataclasses import dataclass, field
from typing import Any

from doeff import Effect, Pass, Resume, do
from doeff.effects import ask, tell
from doeff_test_target.effects import ReadFixtureValue, RecordFixtureEvent

ProtocolHandler = Callable[[Any, Any], Any]


@dataclass
class ProductionFixtureRuntime:
    """Runtime state used by production fixture handlers."""

    fallback_env: dict[str, Any] = field(default_factory=dict)
    recorded_events: MutableSequence[str] = field(default_factory=list)

    def handle_read_value(self, effect: ReadFixtureValue, k):
        if effect.key in self.fallback_env:
            return (yield Resume(k, self.fallback_env[effect.key]))

        value = yield ask(effect.key)
        return (yield Resume(k, value))

    def handle_record_event(self, effect: RecordFixtureEvent, k):
        self.recorded_events.append(effect.message)
        yield tell(effect.message)
        return (yield Resume(k, None))


def production_handlers(
    *,
    fallback_env: Mapping[str, Any] | None = None,
    recorded_events: MutableSequence[str] | None = None,
    runtime: ProductionFixtureRuntime | None = None,
) -> ProtocolHandler:
    """Build a production protocol handler for doeff-test-target fixture effects."""

    active_runtime = runtime or ProductionFixtureRuntime(
        fallback_env=dict(fallback_env or {}),
        recorded_events=recorded_events if recorded_events is not None else [],
    )

    @do
    def handler(effect: Effect, k: Any):
        if isinstance(effect, ReadFixtureValue):
            return (yield from active_runtime.handle_read_value(effect, k))
        if isinstance(effect, RecordFixtureEvent):
            return (yield from active_runtime.handle_record_event(effect, k))
        return (yield Pass())

    return handler


__all__ = [
    "ProductionFixtureRuntime",
    "ProtocolHandler",
    "production_handlers",
]
