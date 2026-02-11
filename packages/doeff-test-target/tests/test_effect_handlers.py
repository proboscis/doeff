import sys
from pathlib import Path

from doeff import run_with_handler_map

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_test_target.effects import (
    ReadFixtureValue,
    RecordFixtureEvent,
    read_fixture_value,
    record_fixture_event,
)
from doeff_test_target.handlers import mock_handlers, production_handlers
from doeff_test_target.scenarios.target_effects import fixture_effect_roundtrip


def test_effects_module_exports_domain_effects():
    assert isinstance(read_fixture_value("alpha"), ReadFixtureValue)
    assert isinstance(record_fixture_event("fixture-event"), RecordFixtureEvent)


def test_handlers_init_exports_required_factories():
    assert callable(production_handlers)
    assert callable(mock_handlers)


def test_mock_handlers_execute_fixture_effect_roundtrip():
    result = run_with_handler_map(
        fixture_effect_roundtrip("alpha"),
        mock_handlers(seed_env={"alpha": "alpha-from-mock"}),
    )
    assert result.value == "alpha-from-mock"


def test_production_handlers_delegate_to_reader_and_writer():
    recorded_events: list[str] = []
    result = run_with_handler_map(
        fixture_effect_roundtrip("service_name"),
        production_handlers(recorded_events=recorded_events),
        env={"service_name": "doeff-test-target"},
    )
    assert result.value == "doeff-test-target"
    assert recorded_events == ["visited:service_name"]
