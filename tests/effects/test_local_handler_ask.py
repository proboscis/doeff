from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff import (
    Ask,
    Effect,
    EffectBase,
    Local,
    Pass,
    Resume,
    Tell,
    WithHandler,
    do,
)
from tests._run_helpers import run_with_defaults


@dataclass(frozen=True)
class ReplaceAudioTrackForLocalTypedFilter(EffectBase):
    duck_original: float
    duck_speech: float


def _run_with_handlers(
    program,
    extra_handlers: list[Any],
    env: dict[str, Any],
):
    wrapped = program
    for h in extra_handlers:
        wrapped = WithHandler(h, wrapped)
    return run_with_defaults(wrapped, env=env)


def test_handler_ask_sees_local_scope() -> None:
    """Handler-emitted Ask resolves against Local overrides."""

    @dataclass(frozen=True)
    class Ping(EffectBase):
        pass

    @do
    def ping_handler(effect: Effect, k):
        if not isinstance(effect, Ping):
            yield Pass(effect, k)
            return
        value = yield Ask("config")
        return (yield Resume(k, value))

    @do
    def body():
        return (yield Ping())

    result = _run_with_handlers(
        Local({"config": "from_local"}, body()),
        [ping_handler],
        env={},
    )
    assert result.is_ok()
    assert result.value == "from_local"


def test_handler_ask_falls_through_to_outer_env() -> None:
    """Handler Ask for a non-overridden key resolves to outer env."""

    @dataclass(frozen=True)
    class Ping(EffectBase):
        pass

    @do
    def ping_handler(effect: Effect, k):
        if not isinstance(effect, Ping):
            yield Pass(effect, k)
            return
        value = yield Ask("outer_key")
        return (yield Resume(k, value))

    @do
    def body():
        return (yield Ping())

    result = _run_with_handlers(
        Local({"other": "irrelevant"}, body()),
        [ping_handler],
        env={"outer_key": "from_env"},
    )
    assert result.is_ok()
    assert result.value == "from_env"


def test_handler_ask_nested_local() -> None:
    """Handler Ask resolves the innermost Local override."""

    @dataclass(frozen=True)
    class Ping(EffectBase):
        pass

    @do
    def ping_handler(effect: Effect, k):
        if not isinstance(effect, Ping):
            yield Pass(effect, k)
            return
        value = yield Ask("key")
        return (yield Resume(k, value))

    @do
    def body():
        return (yield Ping())

    program = Local({"key": "outer"}, Local({"key": "inner"}, body()))
    result = _run_with_handlers(
        program,
        [ping_handler],
        env={"key": "root"},
    )
    assert result.is_ok()
    assert result.value == "inner"


def test_multiple_handlers_ask_in_local() -> None:
    """Multiple handlers emitting Ask should share the same Local scope."""

    @dataclass(frozen=True)
    class EffA(EffectBase):
        pass

    @dataclass(frozen=True)
    class EffB(EffectBase):
        pass

    @do
    def handler_a(effect: Effect, k):
        if not isinstance(effect, EffA):
            yield Pass(effect, k)
            return
        value = yield Ask("key_a")
        return (yield Resume(k, value))

    @do
    def handler_b(effect: Effect, k):
        if not isinstance(effect, EffB):
            yield Pass(effect, k)
            return
        value = yield Ask("key_b")
        return (yield Resume(k, value))

    @do
    def body():
        a = yield EffA()
        b = yield EffB()
        return (a, b)

    result = _run_with_handlers(
        Local({"key_a": "alpha", "key_b": "beta"}, body()),
        [handler_a, handler_b],
        env={},
    )
    assert result.is_ok()
    assert result.value == ("alpha", "beta")


def test_handler_ask_lazy_value_in_local() -> None:
    """Handler Ask resolves lazy Local values exactly once."""

    @dataclass(frozen=True)
    class Ping(EffectBase):
        pass

    call_count = 0

    @do
    def expensive():
        nonlocal call_count
        call_count += 1
        if False:
            yield
        return 42

    @do
    def ping_handler(effect: Effect, k):
        if not isinstance(effect, Ping):
            yield Pass(effect, k)
            return
        value = yield Ask("lazy_svc")
        return (yield Resume(k, value))

    @do
    def body():
        return (yield Ping())

    result = _run_with_handlers(
        Local({"lazy_svc": expensive()}, body()),
        [ping_handler],
        env={},
    )
    assert result.is_ok()
    assert result.value == 42
    assert call_count == 1


def test_local_eval_keeps_typed_handler_filtering() -> None:
    """GetHandlers->Eval must not duplicate handler chains around Local."""

    seen_effect_types: list[str] = []

    @do
    def replace_audio_handler(effect: Effect, k):
        if not isinstance(effect, ReplaceAudioTrackForLocalTypedFilter):
            yield Pass(effect, k)
            return
        seen_effect_types.append(type(effect).__name__)
        value = f"{effect.duck_original}:{effect.duck_speech}"
        return (yield Resume(k, value))

    @do
    def memo_rewriter(effect: Effect, k):
        yield Pass(effect, k)

    @do
    def body():
        yield Tell({"msg": "slog"})
        return (yield ReplaceAudioTrackForLocalTypedFilter(duck_original=0.25, duck_speech=0.6))

    wrapped = WithHandler(
        replace_audio_handler,
        WithHandler(memo_rewriter, Local({"unused": "value"}, body())),
    )
    result = run_with_defaults(wrapped, env={})

    assert result.is_ok()
    assert result.value == "0.25:0.6"
    assert seen_effect_types == ["ReplaceAudioTrackForLocalTypedFilter"]


def test_handler_emitted_local_overrides_effect_site_scope() -> None:
    """Local emitted by handler should evaluate in handler call-site scope."""

    @dataclass(frozen=True)
    class Ping(EffectBase):
        pass

    @do
    def ping_handler(effect: Effect, k):
        if not isinstance(effect, Ping):
            yield Pass(effect, k)
            return
        value = yield Local({"config": "from_handler_local"}, Ask("config"))
        return (yield Resume(k, value))

    @do
    def body():
        return (yield Ping())

    program = Local({"config": "from_effect_site"}, body())
    result = _run_with_handlers(
        program,
        [ping_handler],
        env={"config": "from_env"},
    )
    assert result.is_ok()
    assert result.value == "from_handler_local"


def test_handler_emitted_local_scope_is_popped_after_eval() -> None:
    """Handler-local scope should be removed after Local(...) evaluation completes."""

    @dataclass(frozen=True)
    class Ping(EffectBase):
        pass

    @do
    def ping_handler(effect: Effect, k):
        if not isinstance(effect, Ping):
            yield Pass(effect, k)
            return
        inner = yield Local({"config": "handler_local"}, Ask("config"))
        outer = yield Ask("config")
        return (yield Resume(k, (inner, outer)))

    @do
    def body():
        return (yield Ping())

    program = Local({"config": "effect_site_local"}, body())
    result = _run_with_handlers(
        program,
        [ping_handler],
        env={"config": "env"},
    )
    assert result.is_ok()
    assert result.value == ("handler_local", "effect_site_local")
