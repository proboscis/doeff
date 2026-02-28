from dataclasses import dataclass

import doeff_vm
import pytest

from doeff import Effect, EffectBase, do
from doeff import rust_vm
from doeff.rust_vm import WithHandler


@dataclass(frozen=True)
class TellFx(EffectBase):
    message: str


@dataclass(frozen=True)
class TellChildFx(TellFx):
    pass


@dataclass(frozen=True)
class AskFx(EffectBase):
    key: str


@dataclass(frozen=True)
class GetFx(EffectBase):
    key: str


@do
def fallback_handler(effect: Effect, k):
    if isinstance(effect, TellFx):
        return (yield doeff_vm.Resume(k, f"fallback_tell:{effect.message}"))
    if isinstance(effect, AskFx):
        return (yield doeff_vm.Resume(k, f"fallback_ask:{effect.key}"))
    if isinstance(effect, GetFx):
        return (yield doeff_vm.Resume(k, f"fallback_get:{effect.key}"))
    delegated = yield doeff_vm.Delegate()
    return (yield doeff_vm.Resume(k, delegated))


@do
def _ask_program():
    return (yield AskFx("k"))


@do
def _tell_program(message: str):
    return (yield TellFx(message))


@do
def _tell_child_program():
    return (yield TellChildFx("child"))


def test_extract_handler_effect_types() -> None:
    @do
    def tell_handler(effect: TellFx, k):
        yield doeff_vm.Pass()

    @do
    def union_handler(effect: TellFx | AskFx, k):
        yield doeff_vm.Pass()

    @do
    def any_effect_handler(effect: Effect, k):
        yield doeff_vm.Pass()

    def no_annotation_handler(effect, k):
        return effect, k

    assert rust_vm._extract_handler_effect_types(tell_handler) == (TellFx,)
    assert rust_vm._extract_handler_effect_types(union_handler) == (TellFx, AskFx)
    assert rust_vm._extract_handler_effect_types(any_effect_handler) is None
    assert rust_vm._extract_handler_effect_types(no_annotation_handler) is None


def test_with_handler_passes_types_to_vm() -> None:
    @do
    def tell_handler(effect: TellFx, k):
        yield doeff_vm.Pass()

    @do
    def any_effect_handler(effect: Effect, k):
        yield doeff_vm.Pass()

    typed_ctrl = WithHandler(tell_handler, _tell_program("x"))
    catch_all_ctrl = WithHandler(any_effect_handler, _tell_program("x"))

    assert typed_ctrl.types == (TellFx,)
    assert catch_all_ctrl.types is None


def test_raw_doeff_vm_with_handler_accepts_types_kwarg() -> None:
    @do
    def tell_handler(effect: TellFx, k):
        yield doeff_vm.Pass()

    ctrl = doeff_vm.WithHandler(tell_handler, _tell_program("x"), types=(TellFx,))
    assert ctrl.types == (TellFx,)


def test_raw_doeff_vm_with_handler_validates_types_kwarg() -> None:
    @do
    def tell_handler(effect: TellFx, k):
        yield doeff_vm.Pass()

    with pytest.raises(TypeError, match="WithHandler.types must contain only Python type objects"):
        doeff_vm.WithHandler(tell_handler, _tell_program("x"), types=(TellFx, "bad"))


@pytest.mark.asyncio
async def test_with_handler_type_filter_skips_non_matching_effect(parameterized_interpreter) -> None:
    seen: list[str] = []

    @do
    def tell_only_handler(effect: TellFx, k):
        seen.append(type(effect).__name__)
        return (yield doeff_vm.Resume(k, f"handled_tell:{effect.message}"))

    wrapped = WithHandler(
        fallback_handler,
        WithHandler(tell_only_handler, _ask_program()),
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "fallback_ask:k"
    assert seen == []


@pytest.mark.asyncio
async def test_with_handler_type_filter_matches_subclass(parameterized_interpreter) -> None:
    seen: list[str] = []

    @do
    def tell_only_handler(effect: TellFx, k):
        seen.append(type(effect).__name__)
        return (yield doeff_vm.Resume(k, f"handled_tell:{effect.message}"))

    wrapped = WithHandler(
        fallback_handler,
        WithHandler(tell_only_handler, _tell_child_program()),
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "handled_tell:child"
    assert seen == ["TellChildFx"]


@pytest.mark.asyncio
async def test_with_handler_effect_annotation_keeps_backward_compat(parameterized_interpreter) -> None:
    seen: list[str] = []

    @do
    def catch_all_handler(effect: Effect, k):
        seen.append(type(effect).__name__)
        yield doeff_vm.Pass()

    @do
    def body():
        ask_value = yield AskFx("a")
        tell_value = yield TellFx("t")
        return (ask_value, tell_value)

    wrapped = WithHandler(fallback_handler, WithHandler(catch_all_handler, body()))
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == ("fallback_ask:a", "fallback_tell:t")
    assert seen == ["AskFx", "TellFx"]


@pytest.mark.asyncio
async def test_with_handler_union_type_filter(parameterized_interpreter) -> None:
    seen: list[str] = []

    @do
    def union_handler(effect: TellFx | AskFx, k):
        seen.append(type(effect).__name__)
        yield doeff_vm.Pass()

    @do
    def body():
        tell_value = yield TellFx("t")
        ask_value = yield AskFx("a")
        get_value = yield GetFx("g")
        return (tell_value, ask_value, get_value)

    wrapped = WithHandler(fallback_handler, WithHandler(union_handler, body()))
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == ("fallback_tell:t", "fallback_ask:a", "fallback_get:g")
    assert seen == ["TellFx", "AskFx"]


@pytest.mark.asyncio
async def test_with_handler_filter_still_allows_runtime_pass(parameterized_interpreter) -> None:
    seen: list[str] = []

    @do
    def tell_handler(effect: TellFx, k):
        seen.append(effect.message)
        if effect.message.startswith("ok"):
            return (yield doeff_vm.Resume(k, f"handled_tell:{effect.message}"))
        yield doeff_vm.Pass()

    wrapped = WithHandler(fallback_handler, WithHandler(tell_handler, _tell_program("skip")))
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "fallback_tell:skip"
    assert seen == ["skip"]


@pytest.mark.asyncio
async def test_stacked_handlers_with_distinct_type_filters(parameterized_interpreter) -> None:
    seen: list[str] = []

    @do
    def tell_handler(effect: TellFx, k):
        seen.append(f"tell:{type(effect).__name__}")
        return (yield doeff_vm.Resume(k, f"tell:{effect.message}"))

    @do
    def ask_handler(effect: AskFx, k):
        seen.append(f"ask:{type(effect).__name__}")
        return (yield doeff_vm.Resume(k, f"ask:{effect.key}"))

    wrapped_for_ask = WithHandler(
        fallback_handler,
        WithHandler(ask_handler, WithHandler(tell_handler, _ask_program())),
    )
    ask_result = await parameterized_interpreter.run_async(wrapped_for_ask)
    assert ask_result.is_ok
    assert ask_result.value == "ask:k"

    wrapped_for_tell = WithHandler(
        fallback_handler,
        WithHandler(ask_handler, WithHandler(tell_handler, _tell_program("t"))),
    )
    tell_result = await parameterized_interpreter.run_async(wrapped_for_tell)
    assert tell_result.is_ok
    assert tell_result.value == "tell:t"

    assert seen == ["ask:AskFx", "tell:TellFx"]
