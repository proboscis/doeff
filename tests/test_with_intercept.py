from __future__ import annotations

from dataclasses import dataclass

import doeff_vm
import pytest

from doeff import EffectBase, Listen, Mask, MaskBehind, Override, Tell, WriterTellEffect, do


@dataclass(frozen=True)
class Ping(EffectBase):
    label: str


def _delegate_then_resume(effect, k):
    delegated = yield doeff_vm.Delegate()
    return (yield doeff_vm.Resume(k, delegated))


def ping_outer_handler(effect, k):
    if isinstance(effect, Ping):
        return (yield doeff_vm.Resume(k, f"outer:{effect.label}"))
    return (yield from _delegate_then_resume(effect, k))


def ping_inner_handler(effect, k):
    if isinstance(effect, Ping):
        return (yield doeff_vm.Resume(k, f"inner:{effect.label}"))
    return (yield from _delegate_then_resume(effect, k))


@pytest.mark.asyncio
async def test_override_logging_observes_and_forwards(parameterized_interpreter) -> None:
    seen: list[str] = []

    def observe_tell(effect, k):
        if isinstance(effect, WriterTellEffect):
            seen.append(effect.message)
        delegated = yield doeff_vm.Delegate()
        return (yield doeff_vm.Resume(k, delegated))

    @do
    def body():
        yield Tell("first")
        yield Tell("second")
        return "ok"

    @do
    def main():
        wrapped = Override(handler=observe_tell, effect_types=[WriterTellEffect], body=body())
        return (yield Listen(wrapped))

    result = await parameterized_interpreter.run_async(main())
    assert result.is_ok
    assert result.value.value == "ok"
    assert result.value.log == ["first", "second"]
    assert seen == ["first", "second"]


@pytest.mark.asyncio
async def test_nested_overrides_compose(parameterized_interpreter) -> None:
    inner_seen: list[str] = []
    outer_seen: list[str] = []

    def inner(effect, k):
        if isinstance(effect, WriterTellEffect):
            inner_seen.append(effect.message)
        delegated = yield doeff_vm.Delegate()
        return (yield doeff_vm.Resume(k, delegated))

    def outer(effect, k):
        if isinstance(effect, WriterTellEffect):
            outer_seen.append(effect.message)
        delegated = yield doeff_vm.Delegate()
        return (yield doeff_vm.Resume(k, delegated))

    @do
    def body():
        yield Tell("event")
        return "done"

    @do
    def main():
        wrapped = Override(
            handler=outer,
            effect_types=[WriterTellEffect],
            body=Override(handler=inner, effect_types=[WriterTellEffect], body=body()),
        )
        return (yield Listen(wrapped))

    result = await parameterized_interpreter.run_async(main())
    assert result.is_ok
    assert result.value.value == "done"
    assert result.value.log == ["event"]
    assert inner_seen == ["event"]
    assert outer_seen == ["event"]


@pytest.mark.asyncio
async def test_mask_skips_next_matching_handler(parameterized_interpreter) -> None:
    def first_handler(effect, k):
        if isinstance(effect, Ping):
            return (yield doeff_vm.Resume(k, f"first:{effect.label}"))
        return (yield from _delegate_then_resume(effect, k))

    def second_handler(effect, k):
        if isinstance(effect, Ping):
            return (yield doeff_vm.Resume(k, f"second:{effect.label}"))
        return (yield from _delegate_then_resume(effect, k))

    @do
    def body():
        return (yield Ping("x"))

    wrapped = doeff_vm.WithHandler(
        first_handler,
        doeff_vm.WithHandler(second_handler, Mask([Ping], body())),
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "first:x"


@pytest.mark.asyncio
async def test_override_matches_explicit_maskbehind_desugaring(parameterized_interpreter) -> None:
    def overriding_handler(effect, k):
        if isinstance(effect, Ping):
            delegated = yield doeff_vm.Delegate()
            return (yield doeff_vm.Resume(k, f"override:{delegated}"))
        return (yield from _delegate_then_resume(effect, k))

    @do
    def body():
        return (yield Ping("p"))

    explicit = doeff_vm.WithHandler(
        overriding_handler,
        MaskBehind([Ping], body()),
    )
    sugar = Override(handler=overriding_handler, effect_types=[Ping], body=body())

    explicit_result = await parameterized_interpreter.run_async(
        doeff_vm.WithHandler(ping_outer_handler, explicit)
    )
    sugar_result = await parameterized_interpreter.run_async(
        doeff_vm.WithHandler(ping_outer_handler, sugar)
    )

    assert explicit_result.is_ok
    assert sugar_result.is_ok
    assert explicit_result.value == "override:outer:p"
    assert sugar_result.value == explicit_result.value


@pytest.mark.asyncio
async def test_catch_all_handler_plus_delegate_observes_untyped(parameterized_interpreter) -> None:
    seen_types: list[str] = []

    def catch_all(effect, k):
        seen_types.append(type(effect).__name__)
        delegated = yield doeff_vm.Delegate()
        return (yield doeff_vm.Resume(k, delegated))

    @do
    def body():
        yield Tell("hello")
        return (yield Ping("z"))

    wrapped = doeff_vm.WithHandler(
        ping_inner_handler,
        doeff_vm.WithHandler(catch_all, body()),
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "inner:z"
    assert any("Tell" in name for name in seen_types)
    assert "Ping" in seen_types


def test_with_intercept_removed_from_public_api() -> None:
    import doeff

    with pytest.raises(AttributeError):
        doeff.WithIntercept
    assert not hasattr(doeff_vm, "WithIntercept")
