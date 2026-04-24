"""Tests for Pure - the immediate value effect (Pure case of Free monad)."""

import pytest

from doeff import Program, do
from doeff_vm import Pure
from tests._run_helpers import run_with_defaults


def test_pure_effect_returns_value():
    @do
    def pure_program() -> Program[int]:
        result = yield Pure(42)
        return result

    run_result = run_with_defaults(pure_program())

    assert run_result.is_ok()
    assert run_result.value == 42


def test_pure_effect_with_complex_value():
    complex_value = {"key": "value", "nested": [1, 2, 3]}

    @do
    def pure_program() -> Program[dict]:
        result = yield Pure(complex_value)
        return result

    run_result = run_with_defaults(pure_program())

    assert run_result.is_ok()
    assert run_result.value == complex_value


def test_pure_effect_with_none():
    @do
    def pure_program() -> Program[None]:
        result = yield Pure(None)
        return result

    run_result = run_with_defaults(pure_program())

    assert run_result.is_ok()
    assert run_result.value is None


def test_pure_factory_function():
    @do
    def pure_program() -> Program[str]:
        result = yield Pure("hello")
        return result

    run_result = run_with_defaults(pure_program())

    assert run_result.is_ok()
    assert run_result.value == "hello"


def test_pure_effect_in_composition():
    from doeff import Ask

    @do
    def composed_program() -> Program[str]:
        name = yield Ask("name")
        greeting = yield Pure(f"Hello, {name}!")
        return greeting

    run_result = run_with_defaults(
        composed_program(), env={"name": "World"}
    )

    assert run_result.is_ok()
    assert run_result.value == "Hello, World!"


def test_pure_effect_immutable():
    effect = Pure(42)

    with pytest.raises(
        AttributeError,
        match=r"can't set attribute|cannot assign to field|not writable|readonly",
    ):
        effect.value = 100  # type: ignore
