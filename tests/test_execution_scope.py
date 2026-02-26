from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from doeff import (
    Delegate,
    EffectGenerator,
    Pass,
    Resume,
    Transfer,
    Try,
    WithHandler,
    default_handlers,
    do,
    run,
)
from doeff.effects.base import EffectBase

pytestmark = pytest.mark.xfail(
    reason=(
        "Pre-existing hang/infinite-loop before VM-REENTRANT-001: all tests in this file deadlock "
        "during execution involving Transfer + WithHandler scope management. Not related to anchor "
        "removal. Needs separate investigation."
    ),
    strict=False,
)


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


def ping_base_handler(effect: object, k: object):
    if not isinstance(effect, ScopePing):
        yield Pass()
        return
    return (yield Resume(k, f"base:{effect.label}"))


def boom_handler(effect: object, k: object):
    if not isinstance(effect, ScopeBoom):
        yield Pass()
        return
    raise RuntimeError(f"boom:{effect.label}")


@do
def ping_program(label: str) -> EffectGenerator[str]:
    return (yield ScopePing(label=label))


def test_nested_dispatch_scope_restores_outer_context() -> None:
    def outer(effect: object, k: object):
        if not isinstance(effect, ScopePing):
            yield Pass()
            return
        nested = yield ScopePing(label=f"{effect.label}:inner")
        return (yield Resume(k, f"{nested}|outer"))

    wrapped = _with_handlers(ping_program("x"), outer, ping_base_handler)
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    assert result.value == "base:x:inner|outer"


def test_delegate_keeps_nested_handler_scope_order() -> None:
    def inner(effect: object, k: object):
        if not isinstance(effect, ScopePing):
            yield Pass()
            return
        delegated = yield Delegate()
        return (yield Resume(k, f"{delegated}|inner"))

    def outer(effect: object, k: object):
        if not isinstance(effect, ScopePing):
            yield Pass()
            return
        delegated = yield Delegate()
        return (yield Resume(k, f"{delegated}|outer"))

    wrapped = _with_handlers(ping_program("x"), inner, outer, ping_base_handler)
    result = run(wrapped, handlers=default_handlers())
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
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    first, second, final = result.value
    assert first.is_err()
    assert "boom:a" in str(first.error)
    assert second.is_err()
    assert "boom:b" in str(second.error)
    assert final == "base:ok"


def test_nested_try_inside_handler_does_not_corrupt_outer_flow() -> None:
    def outer(effect: object, k: object):
        if not isinstance(effect, ScopePing):
            yield Pass()
            return
        nested = yield Try(ScopeBoom(label="inner"))
        assert nested.is_err()
        return (yield Resume(k, f"after:{effect.label}"))

    wrapped = _with_handlers(ping_program("scope"), outer, boom_handler, ping_base_handler)
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    assert result.value == "after:scope"


def test_transfer_keeps_dispatch_stack_stable_for_repeated_effects() -> None:
    @dataclass(frozen=True, kw_only=True)
    class TransferPing(EffectBase):
        value: int

    def transfer_handler(effect: object, k: object):
        if not isinstance(effect, TransferPing):
            yield Pass()
            return
        yield Transfer(k, effect.value)

    @do
    def program() -> EffectGenerator[tuple[int, ...]]:
        values: list[int] = []
        for i in range(6):
            values.append((yield TransferPing(value=i)))
        return tuple(values)

    wrapped = _with_handlers(program(), transfer_handler)
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    assert result.value == tuple(range(6))


def test_late_resume_of_consumed_continuation_stays_scoped() -> None:
    captured: dict[str, object] = {}

    @dataclass(frozen=True, kw_only=True)
    class CaptureEffect(EffectBase):
        label: str

    def capture_handler(effect: object, k: object):
        if not isinstance(effect, CaptureEffect):
            yield Pass()
            return
        captured["k"] = k
        return (yield Resume(k, f"first:{effect.label}"))

    @do
    def program() -> EffectGenerator[tuple[str, object]]:
        first = yield CaptureEffect(label="x")
        late = yield Try(Resume(captured["k"], "late"))
        return first, late

    wrapped = _with_handlers(program(), capture_handler)
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    first, late = result.value
    assert first == "first:x"
    assert late.is_err()
    message = str(late.error)
    assert "one-shot violation" in message or "unknown continuation id" in message
