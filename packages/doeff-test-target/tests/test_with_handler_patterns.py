import sys
from pathlib import Path
from typing import Any

import pytest
from doeff import AskEffect, Effect, Pass, Resume, WithHandler, default_handlers, do, run
from doeff.effects import ask


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_test_target.core.alpha import helper_alpha
from doeff_test_target.core.beta import helper_beta
from doeff_test_target.core.gamma import helper_gamma


@do
def _test_target_pipeline():
    alpha_value = yield helper_alpha()
    beta_value = yield helper_beta()
    gamma_value = yield helper_gamma()
    return {"alpha": alpha_value, "beta": beta_value, "gamma": gamma_value}


@do
def _nested_ask_program():
    outer_value = yield ask("outer")
    inner_value = yield ask("inner")
    return outer_value, inner_value


@do
def _single_ask_program(key: str):
    return (yield ask(key))


def test_withhandler_basic_effect_pipeline_with_mock_handler():
    @do
    def mock_handler(effect: Effect, k):
        if isinstance(effect, AskEffect):
            return (yield Resume(k, f"mock-{effect.key}"))
        return (yield Pass())

    result = run(
        WithHandler(mock_handler, _test_target_pipeline()),
        handlers=default_handlers(),
    )

    assert result.value == {"alpha": "alpha", "beta": "beta", "gamma": "mock-gamma"}


def test_withhandler_nesting_inner_handler_overrides_outer_handler():
    @do
    def outer_handler(effect: Effect, k):
        if isinstance(effect, AskEffect):
            return (yield Resume(k, f"outer-{effect.key}"))
        return (yield Pass())

    @do
    def inner_handler(effect: Effect, k):
        if isinstance(effect, AskEffect) and effect.key == "inner":
            return (yield Resume(k, "inner-mock"))
        return (yield Pass())

    result = run(
        WithHandler(outer_handler, WithHandler(inner_handler, _nested_ask_program())),
        handlers=default_handlers(),
    )

    assert result.value == ("outer-outer", "inner-mock")


def test_withhandler_error_propagation_from_handler():
    class HandlerFailure(RuntimeError):
        pass

    @do
    def failing_handler(effect: Effect, _k):
        if isinstance(effect, AskEffect) and effect.key == "explode":
            raise HandlerFailure("mock handler failure for explode")
        return (yield Pass())

    result = run(
        WithHandler(failing_handler, _single_ask_program("explode")),
        handlers=default_handlers(),
    )

    assert result.is_err()
    assert isinstance(result.error, HandlerFailure)
    assert "explode" in str(result.error)
    with pytest.raises(HandlerFailure, match="explode"):
        _ = result.value


def test_withhandler_delegate_passthrough_uses_default_reader():
    seen_keys: list[str] = []

    @do
    def delegating_handler(effect: Effect, _k):
        if isinstance(effect, AskEffect):
            seen_keys.append(effect.key)
        return (yield Pass())

    result = run(
        WithHandler(delegating_handler, _single_ask_program("service_name")),
        handlers=default_handlers(),
        env={"service_name": "doeff-test-target"},
    )

    assert seen_keys == ["service_name"]
    assert result.value == "doeff-test-target"


def test_withhandler_resume_supports_various_value_types():
    sample_values: list[Any] = [
        "text",
        7,
        {"nested": True},
        ["alpha", "beta"],
        None,
    ]

    for sample_value in sample_values:
        @do
        def typed_mock_handler(effect: Effect, k):
            if isinstance(effect, AskEffect):
                return (yield Resume(k, sample_value))
            return (yield Pass())

        result = run(
            WithHandler(typed_mock_handler, _single_ask_program("any-key")),
            handlers=default_handlers(),
        )

        assert result.value == sample_value
