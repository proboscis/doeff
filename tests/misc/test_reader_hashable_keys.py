from __future__ import annotations

from typing import Protocol

import pytest

from doeff import Ask, Local, ask, do, run
from doeff.effects.reader import AskEffect, LocalEffect
from doeff.rust_vm import default_handlers
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
    assert isinstance(Ask("key"), AskEffect)
    assert isinstance(Ask(42), AskEffect)
    assert isinstance(Ask(("tuple", "key")), AskEffect)


def test_reader_distinguishes_same_str_different_hash() -> None:
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

    key_a = ServiceA()
    key_b = ServiceB()

    @do
    def program() -> EffectGenerator[tuple[str, str]]:
        value_a: str = yield ask(key_a)
        value_b: str = yield ask(key_b)
        return (value_a, value_b)

    result = run(
        program(),
        handlers=default_handlers(),
        env={key_a: "service-a", key_b: "service-b"},
    )
    assert result.is_ok()
    assert result.value == ("service-a", "service-b")


def test_local_builds_rust_local_effect() -> None:
    @do
    def sub_program() -> EffectGenerator[int]:
        return 1

    effect = Local({"key": "value"}, sub_program())
    assert isinstance(effect, LocalEffect)
