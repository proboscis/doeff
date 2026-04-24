from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff import (
    Effect,
    EffectGenerator,
    Pass,
    Resume,
    Transfer,
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
class ScopePing(EffectBase):
    label: str


@dataclass(frozen=True, kw_only=True)
class ScopeBoom(EffectBase):
    label: str


@do
def ping_base_handler(effect: Effect, k: object):
    if not isinstance(effect, ScopePing):
        yield Pass(effect, k)
        return
    return (yield Resume(k, f"base:{effect.label}"))


@do
def boom_handler(effect: Effect, k: object):
    if not isinstance(effect, ScopeBoom):
        yield Pass(effect, k)
        return
    raise RuntimeError(f"boom:{effect.label}")


@do
def ping_program(label: str) -> EffectGenerator[str]:
    return (yield ScopePing(label=label))


def test_nested_dispatch_scope_restores_outer_context() -> None:
    @do
    def outer(effect: Effect, k: object):
        if not isinstance(effect, ScopePing):
            yield Pass(effect, k)
            return
        # Under re-entrant dispatch, this handler sees nested ScopePing effects too.
        # Explicitly pass nested pings through so the base handler resolves them.
        if effect.label.endswith(":inner"):
            yield Pass(effect, k)
            return
        nested = yield ScopePing(label=f"{effect.label}:inner")
        return (yield Resume(k, f"{nested}|outer"))

    wrapped = _with_handlers(ping_program("x"), outer, ping_base_handler)
    result = run_with_defaults(wrapped)
    assert _is_ok(result), result.error
    assert result.value == "base:x:inner|outer"


def test_delegate_keeps_nested_handler_scope_order() -> None:
    @do
    def inner(effect: Effect, k: object):
        if not isinstance(effect, ScopePing):
            yield Pass(effect, k)
            return
        delegated = yield effect
        return (yield Resume(k, f"{delegated}|inner"))

    @do
    def outer(effect: Effect, k: object):
        if not isinstance(effect, ScopePing):
            yield Pass(effect, k)
            return
        delegated = yield effect
        return (yield Resume(k, f"{delegated}|outer"))

    wrapped = _with_handlers(ping_program("x"), inner, outer, ping_base_handler)
    result = run_with_defaults(wrapped)
    assert _is_ok(result), result.error
    assert result.value == "base:x|outer|inner"


def test_try_results_remain_scoped_across_multiple_failures() -> None:
    @do
    def program() -> EffectGenerator[tuple[object, object, str]]:
        first = yield Try(ScopeBoom(label="a"))
        second = yield Try(ScopeBoom(label="b"))
        final = yield ScopePing(label="ok")
        return first, second, final

    wrapped = _with_handlers(program(), boom_handler, ping_base_handler)
    result = run_with_defaults(wrapped)
    assert _is_ok(result), result.error
    first, second, final = result.value
    assert first.is_err()
    assert "boom:a" in str(first.error)
    assert second.is_err()
    assert "boom:b" in str(second.error)
    assert final == "base:ok"


def test_nested_try_inside_handler_does_not_corrupt_outer_flow() -> None:
    @do
    def outer(effect: Effect, k: object):
        if not isinstance(effect, ScopePing):
            yield Pass(effect, k)
            return
        nested = yield Try(ScopeBoom(label="inner"))
        assert nested.is_err()
        return (yield Resume(k, f"after:{effect.label}"))

    wrapped = _with_handlers(ping_program("scope"), outer, boom_handler, ping_base_handler)
    result = run_with_defaults(wrapped)
    assert _is_ok(result), result.error
    assert result.value == "after:scope"


def test_transfer_keeps_dispatch_stack_stable_for_repeated_effects() -> None:
    @dataclass(frozen=True, kw_only=True)
    class TransferPing(EffectBase):
        value: int

    @do
    def transfer_handler(effect: Effect, k: object):
        if not isinstance(effect, TransferPing):
            yield Pass(effect, k)
            return
        yield Transfer(k, effect.value)

    @do
    def program() -> EffectGenerator[tuple[int, ...]]:
        values: list[int] = []
        for i in range(6):
            values.append((yield TransferPing(value=i)))
        return tuple(values)

    wrapped = _with_handlers(program(), transfer_handler)
    result = run_with_defaults(wrapped)
    assert _is_ok(result), result.error
    assert result.value == tuple(range(6))


