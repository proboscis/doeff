"""Tests for the pinjected bridge effect pipeline without importing pinjected."""


from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

from doeff import Ask, AskEffect, Effect, Pass, Resume, WithHandler, default_handlers, do, run

_EFFECT_FAILURE_TYPE_NAMES = {"EffectFailure", "EffectFailureError"}


def _unwrap_effect_failure(error: BaseException) -> BaseException:
    """Unwrap runtime-specific EffectFailure wrappers when available."""
    if error.__class__.__name__ in _EFFECT_FAILURE_TYPE_NAMES:
        cause = getattr(error, "cause", None)
        if isinstance(cause, BaseException):
            return cause
    return error


@dataclass
class MockPinjectedGraph:
    """Simple graph stub used by tests to emulate dependency resolution."""

    bindings: dict[str, Any]
    resolve_calls: list[str] = field(default_factory=list)

    def resolve(self, binding_name: str) -> Any:
        self.resolve_calls.append(binding_name)
        if binding_name not in self.bindings:
            raise KeyError(binding_name)
        return self.bindings[binding_name]


def _mock_pinjected_handler(graph: MockPinjectedGraph):
    """Return a WithHandler-compatible function for bridge Ask effects."""

    @do
    def handler(effect: Effect, k):
        if isinstance(effect, AskEffect):
            if effect.key == "pinjected_graph":
                return (yield Resume(k, graph))

            if isinstance(effect.key, str) and effect.key.startswith("pinjected_binding:"):
                binding_name = effect.key.split(":", 1)[1]
                return (yield Resume(k, graph.resolve(binding_name)))

        return (yield Pass())

    return handler


@do
def _bridge_lookup(binding_name: str) -> Generator[Any, Any, dict[str, Any]]:
    graph = yield Ask("pinjected_graph")
    binding = yield Ask(f"pinjected_binding:{binding_name}")
    return {"graph": graph, "binding": binding}


def test_bridge_pipeline_resolves_graph_and_binding() -> None:
    graph = MockPinjectedGraph({"service": "service-instance"})
    result = run(
        WithHandler(_mock_pinjected_handler(graph), _bridge_lookup("service")),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value["graph"] is graph
    assert result.value["binding"] == "service-instance"
    assert graph.resolve_calls == ["service"]


def test_bridge_pipeline_resolves_multiple_bindings_in_order() -> None:
    graph = MockPinjectedGraph({"alpha": 10, "beta": 20})

    @do
    def bridge_pipeline() -> Generator[Any, Any, tuple[Any, Any, Any]]:
        yield Ask("pinjected_graph")
        alpha = yield Ask("pinjected_binding:alpha")
        beta = yield Ask("pinjected_binding:beta")
        alpha_again = yield Ask("pinjected_binding:alpha")
        return alpha, beta, alpha_again

    result = run(
        WithHandler(_mock_pinjected_handler(graph), bridge_pipeline()),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value == (10, 20, 10)
    assert graph.resolve_calls == ["alpha", "beta", "alpha"]


def test_bridge_pipeline_delegates_non_bridge_ask_to_reader_env() -> None:
    graph = MockPinjectedGraph({"service": "graph-service"})

    @do
    def bridge_pipeline() -> Generator[Any, Any, tuple[str, str]]:
        resolved = yield Ask("pinjected_binding:service")
        env_value = yield Ask("runtime_value")
        return resolved, env_value

    result = run(
        WithHandler(_mock_pinjected_handler(graph), bridge_pipeline()),
        handlers=default_handlers(),
        env={"runtime_value": "from-env"},
    )

    assert result.is_ok()
    assert result.value == ("graph-service", "from-env")
    assert graph.resolve_calls == ["service"]


def test_bridge_pipeline_reports_missing_binding_error() -> None:
    graph = MockPinjectedGraph({"service": "ok"})
    result = run(
        WithHandler(_mock_pinjected_handler(graph), _bridge_lookup("missing")),
        handlers=default_handlers(),
    )

    assert result.is_err()
    error = _unwrap_effect_failure(result.error)
    assert isinstance(error, KeyError)
    assert error.args == ("missing",)
    assert graph.resolve_calls == ["missing"]
