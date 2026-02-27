from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff import (
    Delegate,
    EffectGenerator,
    Override,
    Pass,
    Pure,
    Resume,
    Try,
    WithHandler,
    default_handlers,
    do,
    run,
)
from doeff.effects.base import EffectBase


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


def inner_ping_handler(effect: object, k: object):
    if not isinstance(effect, InnerPing):
        yield Pass()
        return
    return (yield Resume(k, f"inner:{effect.label}"))


def outer_ping_handler(effect: object, k: object):
    if not isinstance(effect, OuterPing):
        yield Pass()
        return
    first = yield InnerPing(label=f"{effect.label}:a")
    second = yield InnerPing(label=f"{effect.label}:b")
    return (yield Resume(k, f"{first}|{second}"))


def boom_handler(effect: object, k: object):
    if not isinstance(effect, ScopeBoom):
        yield Pass()
        return
    raise RuntimeError(f"boom:{effect.label}")


def outer_boom_handler(effect: object, k: object):
    if not isinstance(effect, OuterPing):
        yield Pass()
        return
    inner = yield Try(ScopeBoom(label="inner"))
    return (yield Resume(k, inner))


def scope_probe_handler(effect: object, k: object):
    if not isinstance(effect, ScopeProbe):
        yield Pass()
        return
    # Return DoExpr directly so VM must evaluate handler return in nested context.
    return Pure(f"handled:{effect.label}")


def scope_trigger_handler(effect: object, k: object):
    if not isinstance(effect, ScopeTrigger):
        yield Pass()
        return
    return (yield Resume(k, f"trigger:{effect.label}"))


@do
def _outer_ping_program() -> EffectGenerator[str]:
    return (yield OuterPing(label="root"))


def test_mode_isolation_across_nested_dispatch() -> None:
    wrapped = _with_handlers(_outer_ping_program(), outer_ping_handler, inner_ping_handler)
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    assert result.value == "inner:root:a|inner:root:b"


def test_pending_error_context_not_stolen_by_nested_dispatch() -> None:
    @do
    def program() -> EffectGenerator[tuple[object, object]]:
        first = yield Try(OuterPing(label="boom-wrapper"))
        second = yield Try(ScopeBoom(label="outer"))
        return first, second

    wrapped = _with_handlers(program(), outer_boom_handler, boom_handler)
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    first, second = result.value
    assert first.is_ok()
    assert first.value.is_err()
    assert "boom:inner" in str(first.value.error)
    assert second.is_err()
    assert "boom:outer" in str(second.error)


def test_interceptor_eval_depth_isolated_per_context() -> None:
    seen: list[str] = []

    def observer_handler(effect: object, k: object):
        if isinstance(effect, ScopeProbe):
            seen.append(effect.label)
        delegated = yield Delegate()
        return (yield Resume(k, delegated))

    @do
    def program() -> EffectGenerator[str]:
        return (yield ScopeProbe(label="depth-a"))

    wrapped = _with_handlers(
        Override(handler=observer_handler, effect_types=[ScopeProbe], body=program()),
        scope_probe_handler,
    )
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    assert result.value == "handled:depth-a"
    assert seen == ["depth-a"]


def test_interceptor_skip_stack_isolated_per_context() -> None:
    seen: list[str] = []

    def observer_handler(effect: object, k: object):
        if isinstance(effect, InnerPing):
            seen.append(effect.label)
        delegated = yield Delegate()
        return (yield Resume(k, delegated))

    @do
    def program() -> EffectGenerator[tuple[str, str]]:
        first = yield InnerPing(label="first")
        second = yield InnerPing(label="second")
        return first, second

    wrapped = _with_handlers(
        Override(handler=observer_handler, effect_types=[InnerPing], body=program()),
        inner_ping_handler,
    )
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    assert result.value == ("inner:first", "inner:second")
    assert seen == ["first", "second"]


def test_continuation_capture_resume_path_remains_well_scoped() -> None:
    captured: dict[str, object] = {}

    @dataclass(frozen=True, kw_only=True)
    class CaptureOnce(EffectBase):
        label: str

    def capture_handler(effect: object, k: object):
        if not isinstance(effect, CaptureOnce):
            yield Pass()
            return
        captured["k"] = k
        return (yield Resume(k, f"captured:{effect.label}"))

    @do
    def program() -> EffectGenerator[tuple[str, object]]:
        first = yield CaptureOnce(label="x")
        late = yield Try(Resume(captured["k"], "late-resume"))
        return first, late

    wrapped = _with_handlers(program(), capture_handler)
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    first, late = result.value
    assert first == "captured:x"
    assert late.is_err()
    message = str(late.error)
    assert "one-shot violation" in message or "unknown continuation id" in message


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

    def h3(effect: object, k: object):
        if not isinstance(effect, L3):
            yield Pass()
            return
        return (yield Resume(k, "L3"))

    def h2(effect: object, k: object):
        if not isinstance(effect, L2):
            yield Pass()
            return
        inner = yield L3()
        return (yield Resume(k, f"L2<{inner}>"))

    def h1(effect: object, k: object):
        if not isinstance(effect, L1):
            yield Pass()
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
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    assert result.value == ("L1<L2<L3>>", "L2<L3>", "L3")
