"""Scenarios that exercise doeff-test-target domain effects."""


from doeff import do
from doeff_test_target.effects import read_fixture_value, record_fixture_event


@do
def fixture_effect_roundtrip(key: str):
    """Read a fixture value and record a traceable fixture event."""

    value = yield read_fixture_value(key)
    yield record_fixture_event(f"visited:{key}")
    return value
