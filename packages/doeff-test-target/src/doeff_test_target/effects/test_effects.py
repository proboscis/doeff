"""Effect definitions used by doeff-test-target scenario fixtures."""


from dataclasses import dataclass

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class TestTargetEffectBase(EffectBase):
    """Base class for doeff-test-target fixture effects."""


@dataclass(frozen=True, kw_only=True)
class ReadFixtureValue(TestTargetEffectBase):
    """Read a value used by fixture programs."""

    key: str


@dataclass(frozen=True, kw_only=True)
class RecordFixtureEvent(TestTargetEffectBase):
    """Record a fixture event emitted by a scenario."""

    message: str


def read_fixture_value(key: str) -> ReadFixtureValue:
    """Construct a fixture read effect."""

    return ReadFixtureValue(key=key)


def record_fixture_event(message: str) -> RecordFixtureEvent:
    """Construct a fixture event effect."""

    return RecordFixtureEvent(message=message)


__all__ = [
    "ReadFixtureValue",
    "RecordFixtureEvent",
    "TestTargetEffectBase",
    "read_fixture_value",
    "record_fixture_event",
]
