from __future__ import annotations

from typing import Protocol

import pytest

from doeff import Local, ask, do
from doeff.effects.reader import AskEffect
from doeff.rust_vm import default_handlers, run as vm_run
from doeff.types import EffectGenerator


class SomeFunc(Protocol):
    def __call__(self) -> str: ...


def _impl() -> str:
    return "ok"


@pytest.mark.asyncio
async def test_reader_accepts_hashable_class_keys(interpreter) -> None:
    @do
    def program() -> EffectGenerator[str]:
        func: SomeFunc = yield ask(SomeFunc)
        return func()

    result = await interpreter.run_async(Local({SomeFunc: _impl}, program()))

    assert result.is_ok
    assert result.value == "ok"


def test_ask_rejects_unhashable_keys() -> None:
    with pytest.raises(TypeError, match=r"key must be hashable"):
        ask([])  # type: ignore[arg-type]


def test_ask_uses_single_effect_type_for_hashable_keys() -> None:
    effect = ask(SomeFunc)
    assert isinstance(effect, AskEffect)
    assert effect.key is SomeFunc


class ServiceA:
    def __str__(self) -> str:
        return "Service"

    def __hash__(self) -> int:
        return 1


class ServiceB:
    def __str__(self) -> str:
        return "Service"

    def __hash__(self) -> int:
        return 2


@pytest.mark.asyncio
async def test_reader_distinguishes_same_str_different_hash_keys(interpreter) -> None:
    key_a = ServiceA()
    key_b = ServiceB()

    @do
    def program() -> EffectGenerator[tuple[str, str]]:
        value_a = yield ask(key_a)
        value_b = yield ask(key_b)
        return (value_a, value_b)

    result = await interpreter.run_async(Local({key_a: "A", key_b: "B"}, program()))

    assert result.is_ok
    assert result.value == ("A", "B")


def test_run_env_preserves_hashable_keys() -> None:
    @do
    def program() -> EffectGenerator[str]:
        func: SomeFunc = yield ask(SomeFunc)
        return func()

    result = vm_run(
        program(),
        handlers=default_handlers(),
        env={SomeFunc: _impl},
    )

    assert result.is_ok()
    assert result.value == "ok"
