from __future__ import annotations

from doeff import (
    Ask,
    AskEffect,
    Delegate,
    Effect,
    EffectBase,
    Pass,
    Resume,
    WithHandler,
    default_handlers,
    do,
    run,
)


class _ProbeEffect(EffectBase):
    pass


def test_delegate_returns_outer_value_back_to_inner_handler() -> None:
    observed: dict[str, int] = {}

    @do
    def inner_handler(effect: Effect, _k):
        if isinstance(effect, _ProbeEffect):
            observed["raw"] = yield Delegate()
            return observed["raw"]
        yield Pass()

    @do
    def outer_handler(effect: Effect, k):
        if isinstance(effect, _ProbeEffect):
            return (yield Resume(k, 42))
        yield Pass()

    @do
    def body():
        _ = yield _ProbeEffect()
        return -1  # unreachable (inner handler does not resume k_user)

    result = run(
        WithHandler(outer_handler, WithHandler(inner_handler, body())),
        handlers=default_handlers(),
    )
    assert result.value == 42
    assert observed["raw"] == 42


def test_delegate_allows_transform_then_resume_original_continuation() -> None:
    @do
    def inner_handler(effect: Effect, k):
        if isinstance(effect, _ProbeEffect):
            raw = yield Delegate()
            return (yield Resume(k, raw * 2))
        yield Pass()

    @do
    def outer_handler(effect: Effect, k):
        if isinstance(effect, _ProbeEffect):
            return (yield Resume(k, 21))
        yield Pass()

    @do
    def body():
        x = yield _ProbeEffect()
        return x + 1

    result = run(
        WithHandler(outer_handler, WithHandler(inner_handler, body())),
        handlers=default_handlers(),
    )
    assert result.value == 43


def test_nested_delegate_chain_flows_c_to_b_to_a_to_user() -> None:
    @do
    def handler_a(effect: Effect, k):
        if isinstance(effect, _ProbeEffect):
            raw = yield Delegate()
            return (yield Resume(k, f"{raw}-a"))
        yield Pass()

    @do
    def handler_b(effect: Effect, k):
        if isinstance(effect, _ProbeEffect):
            raw = yield Delegate()
            return (yield Resume(k, f"{raw}-b"))
        yield Pass()

    @do
    def handler_c(effect: Effect, k):
        if isinstance(effect, _ProbeEffect):
            return (yield Resume(k, "c"))
        yield Pass()

    @do
    def body():
        return (yield _ProbeEffect())

    result = run(
        WithHandler(
            handler_c,
            WithHandler(handler_b, WithHandler(handler_a, body())),
        ),
        handlers=default_handlers(),
    )
    assert result.value == "c-b-a"


def test_pass_from_middle_handler_preserves_k_new_for_outer_handler() -> None:
    @do
    def handler_a(effect: Effect, k):
        if isinstance(effect, _ProbeEffect):
            raw = yield Delegate()
            return (yield Resume(k, raw + 5))
        yield Pass()

    @do
    def handler_b(effect: Effect, _k):
        if isinstance(effect, _ProbeEffect):
            yield Pass()
            return -1  # unreachable
        yield Pass()

    @do
    def handler_c(effect: Effect, k):
        if isinstance(effect, _ProbeEffect):
            return (yield Resume(k, 37))
        yield Pass()

    @do
    def body():
        return (yield _ProbeEffect())

    result = run(
        WithHandler(
            handler_c,
            WithHandler(handler_b, WithHandler(handler_a, body())),
        ),
        handlers=default_handlers(),
    )
    assert result.value == 42


def test_delegate_handler_can_return_without_resuming_k_user() -> None:
    body_resumed = {"value": False}

    @do
    def inner_handler(effect: Effect, _k):
        if isinstance(effect, _ProbeEffect):
            raw = yield Delegate()
            return f"inner:{raw}"
        yield Pass()

    @do
    def outer_handler(effect: Effect, k):
        if isinstance(effect, _ProbeEffect):
            return (yield Resume(k, "outer"))
        yield Pass()

    @do
    def body():
        _ = yield _ProbeEffect()
        body_resumed["value"] = True
        return "user-path"

    result = run(
        WithHandler(outer_handler, WithHandler(inner_handler, body())),
        handlers=default_handlers(),
    )
    assert result.value == "inner:outer"
    assert body_resumed["value"] is False


def test_koka_equivalent_delegate_semantics_result_is_85() -> None:
    @do
    def inner_handler(effect: Effect, k):
        if isinstance(effect, AskEffect):
            raw = yield Delegate()
            return (yield Resume(k, raw * 2))
        yield Pass()

    @do
    def outer_handler(effect: Effect, k):
        if isinstance(effect, AskEffect):
            return (yield Resume(k, 42))
        yield Pass()

    @do
    def program():
        x = yield Ask("key")
        return x + 1

    result = run(
        WithHandler(outer_handler, WithHandler(inner_handler, program())),
        handlers=default_handlers(),
    )
    assert result.value == 85
