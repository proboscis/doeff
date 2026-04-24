from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff import (
    Effect,
    EffectGenerator,
    Pass,
    Pure,
    Resume,
    Try,
    WithHandler,
    do,
    )
from doeff_core_effects.effects import EffectBase
from tests._run_helpers import run_with_defaults


def _with_handlers(program: Any, *handlers: Any) -> Any:
    wrapped = program
    for handler in handlers:
        wrapped = WithHandler(handler, wrapped)
    return wrapped


def _is_ok(result: Any) -> bool:
    probe = getattr(result, "is_ok", None)
    if callable(probe):
        return bool(probe())
    return bool(probe)


@dataclass(frozen=True, kw_only=True)
class OuterPing(EffectBase):
    label: str


@dataclass(frozen=True, kw_only=True)
class InnerPing(EffectBase):
    label: str


@dataclass(frozen=True, kw_only=True)
class ScopeBoom(EffectBase):
    label: str


@dataclass(frozen=True, kw_only=True)
class ScopeProbe(EffectBase):
    label: str


@dataclass(frozen=True, kw_only=True)
class ScopeTrigger(EffectBase):
    label: str


@do
def inner_ping_handler(effect: Effect, k: object):
    if not isinstance(effect, InnerPing):
        yield Pass(effect, k)
        return
    return (yield Resume(k, f"inner:{effect.label}"))


@do
def outer_ping_handler(effect: Effect, k: object):
    if not isinstance(effect, OuterPing):
        yield Pass(effect, k)
        return
    first = yield InnerPing(label=f"{effect.label}:a")
    second = yield InnerPing(label=f"{effect.label}:b")
    return (yield Resume(k, f"{first}|{second}"))


@do
def boom_handler(effect: Effect, k: object):
    if not isinstance(effect, ScopeBoom):
        yield Pass(effect, k)
        return
    raise RuntimeError(f"boom:{effect.label}")


@do
def outer_boom_handler(effect: Effect, k: object):
    if not isinstance(effect, OuterPing):
        yield Pass(effect, k)
        return
    inner = yield Try(ScopeBoom(label="inner"))
    return (yield Resume(k, inner))


@do
def scope_probe_handler(effect: Effect, k: object):
    if not isinstance(effect, ScopeProbe):
        yield Pass(effect, k)
        return
    return (yield Resume(k, f"handled:{effect.label}"))


@do
def scope_trigger_handler(effect: Effect, k: object):
    if not isinstance(effect, ScopeTrigger):
        yield Pass(effect, k)
        return
    return (yield Resume(k, f"trigger:{effect.label}"))


@do
def _outer_ping_program() -> EffectGenerator[str]:
    return (yield OuterPing(label="root"))


def test_mode_isolation_across_nested_dispatch() -> None:
    wrapped = _with_handlers(_outer_ping_program(), outer_ping_handler, inner_ping_handler)
    result = run_with_defaults(wrapped)
    assert _is_ok(result), result.error
    assert result.value == "inner:root:a|inner:root:b"


def test_pending_error_context_not_stolen_by_nested_dispatch() -> None:
    @do
    def program() -> EffectGenerator[tuple[object, object]]:
        first = yield Try(OuterPing(label="boom-wrapper"))
        second = yield Try(ScopeBoom(label="outer"))
        return first, second

    wrapped = _with_handlers(program(), outer_boom_handler, boom_handler)
    result = run_with_defaults(wrapped)
    assert _is_ok(result), result.error
    first, second = result.value
    assert first.is_ok()
    assert first.value.is_err()
    assert "boom:inner" in str(first.value.error)
    assert second.is_err()
    assert "boom:outer" in str(second.error)





def test_three_level_nested_structural_isolation() -> None:
    @dataclass(frozen=True, kw_only=True)
    class L1(EffectBase):
        tag: str = "l1"

    @dataclass(frozen=True, kw_only=True)
    class L2(EffectBase):
        tag: str = "l2"

    @dataclass(frozen=True, kw_only=True)
    class L3(EffectBase):
        tag: str = "l3"

    @do
    def h3(effect: Effect, k: object):
        if not isinstance(effect, L3):
            yield Pass(effect, k)
            return
        return (yield Resume(k, "L3"))

    @do
    def h2(effect: Effect, k: object):
        if not isinstance(effect, L2):
            yield Pass(effect, k)
            return
        inner = yield L3()
        return (yield Resume(k, f"L2<{inner}>"))

    @do
    def h1(effect: Effect, k: object):
        if not isinstance(effect, L1):
            yield Pass(effect, k)
            return
        inner = yield L2()
        return (yield Resume(k, f"L1<{inner}>"))

    @do
    def program() -> EffectGenerator[tuple[str, str, str]]:
        first = yield L1()
        second = yield L2()
        third = yield L3()
        return first, second, third

    wrapped = _with_handlers(program(), h1, h2, h3)
    result = run_with_defaults(wrapped)
    assert _is_ok(result), result.error
    assert result.value == ("L1<L2<L3>>", "L2<L3>", "L3")
