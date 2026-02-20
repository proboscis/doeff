from __future__ import annotations

from collections.abc import Generator
from typing import Any

from doeff import Ask, Effect, Program, do
from doeff_vm import Expand, Perform, Pure


@do
def takes_plain(value: Any) -> Generator[Program[Any], Any, Any]:
    if False:  # pragma: no cover
        yield Program.pure(None)
    return value


@do
def takes_program(p: Program[int]) -> Generator[Program[Any], Any, int]:
    value = yield p
    return value


@do
def takes_effect(e: Effect) -> Generator[Program[Any], Any, str]:
    if False:  # pragma: no cover
        yield Program.pure(None)
    return f"{type(e).__name__}:{getattr(e, 'key', '<missing>')}"


@do
def inner_program() -> Generator[Program[Any], Any, int]:
    if False:  # pragma: no cover
        yield Program.pure(None)
    return 7


def test_do_call_returns_expand_doctrl() -> None:
    result = takes_plain(1)
    assert isinstance(result, Expand), f"expected Expand DoCtrl, got {type(result).__name__}"


def test_effect_arg_with_unwrap_yes_becomes_perform() -> None:
    effect = Ask("name")
    result = takes_plain(effect)
    assert isinstance(result, Expand), f"expected Expand DoCtrl, got {type(result).__name__}"
    first = list(result.args)[0]
    assert isinstance(first, Perform), (
        f"expected Perform for effect arg, got {type(first).__name__}"
    )


def test_plain_value_arg_becomes_pure() -> None:
    result = takes_plain(10)
    assert isinstance(result, Expand), f"expected Expand DoCtrl, got {type(result).__name__}"
    first = list(result.args)[0]
    assert isinstance(first, Pure), f"expected Pure for plain value arg, got {type(first).__name__}"
    assert first.value == 10


def test_program_annotated_arg_is_pure_wrapped_not_unwrapped() -> None:
    prog = inner_program()
    result = takes_program(prog)
    assert isinstance(result, Expand), f"expected Expand DoCtrl, got {type(result).__name__}"
    first = list(result.args)[0]
    assert isinstance(first, Pure), (
        f"expected Pure for Program-annotated arg, got {type(first).__name__}"
    )
    assert first.value is prog


def test_effect_annotated_arg_is_pure_wrapped_not_unwrapped() -> None:
    effect = Ask("token")
    result = takes_effect(effect)
    assert isinstance(result, Expand), f"expected Expand DoCtrl, got {type(result).__name__}"
    first = list(result.args)[0]
    assert isinstance(first, Pure), (
        f"expected Pure for Effect-annotated arg, got {type(first).__name__}"
    )
    assert first.value is effect


def test_generator_factory_is_exposed_from_expand_factory_as_pure() -> None:
    result = takes_plain(3)
    assert isinstance(result, Expand), f"expected Expand DoCtrl, got {type(result).__name__}"
    assert isinstance(result.factory, Pure), (
        f"expected Pure factory wrapper, got {type(result.factory).__name__}"
    )
    assert callable(result.factory.value)


def test_strategy_is_cached_on_kleisli_instance() -> None:
    assert hasattr(takes_plain, "_auto_unwrap_strategy"), "expected cached _auto_unwrap_strategy"
