from __future__ import annotations

from dataclasses import dataclass

import doeff_vm
import pytest

from doeff import EffectBase, Listen, Tell, WriterTellEffect, default_handlers, do, run


@dataclass(frozen=True)
class Ping(EffectBase):
    label: str


def ping_handler(effect, k):
    if isinstance(effect, Ping):
        return (yield doeff_vm.Resume(k, f"handled:{effect.label}"))
    delegated = yield doeff_vm.Delegate()
    return (yield doeff_vm.Resume(k, delegated))


def ping_with_tell_handler(effect, k):
    if isinstance(effect, Ping):
        yield Tell(f"handler:{effect.label}")
        return (yield doeff_vm.Resume(k, f"handled:{effect.label}"))
    delegated = yield doeff_vm.Delegate()
    return (yield doeff_vm.Resume(k, delegated))


@do
def _ping_program(label: str):
    return (yield Ping(label))


@pytest.mark.asyncio
async def test_with_intercept_observes_user_tell(parameterized_interpreter) -> None:
    seen: list[str] = []

    def observe(expr):
        if isinstance(expr, WriterTellEffect):
            seen.append(expr.message)
        return expr

    @do
    def body():
        yield Tell("user")
        return "ok"

    wrapped = doeff_vm.WithIntercept(observe, body(), (WriterTellEffect,), "include")
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "ok"
    assert seen == ["user"]


@pytest.mark.asyncio
async def test_with_intercept_observes_handler_tell_cross_cutting(parameterized_interpreter) -> None:
    seen: list[str] = []

    def observe(expr):
        if isinstance(expr, WriterTellEffect):
            seen.append(expr.message)
        return expr

    wrapped = doeff_vm.WithIntercept(
        observe,
        doeff_vm.WithHandler(ping_with_tell_handler, _ping_program("inner")),
        (WriterTellEffect,),
        "include",
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "handled:inner"
    assert seen == ["handler:inner"]


@pytest.mark.asyncio
async def test_with_intercept_type_filter_include(parameterized_interpreter) -> None:
    seen: list[str] = []

    def observe(expr):
        seen.append(type(expr).__name__)
        return expr

    @do
    def body():
        yield Tell("log")
        _ = yield Ping("x")
        return "done"

    wrapped = doeff_vm.WithHandler(
        ping_handler,
        doeff_vm.WithIntercept(observe, body(), (WriterTellEffect,), "include"),
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "done"
    assert seen == ["PyTell"]


@pytest.mark.asyncio
async def test_with_intercept_type_filter_exclude(parameterized_interpreter) -> None:
    seen: list[str] = []

    def observe(expr):
        seen.append(type(expr).__name__)
        return expr

    @do
    def body():
        yield Tell("log")
        _ = yield Ping("x")
        return "done"

    wrapped = doeff_vm.WithHandler(
        ping_handler,
        doeff_vm.WithIntercept(observe, body(), (WriterTellEffect,), "exclude"),
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "done"
    assert "Ping" in seen
    assert "PyTell" not in seen


@pytest.mark.asyncio
async def test_with_intercept_can_filter_doctrl_withhandler(parameterized_interpreter) -> None:
    seen: list[str] = []
    with_handler_type = type(doeff_vm.WithHandler(ping_handler, _ping_program("probe")))

    def observe(expr):
        if isinstance(expr, with_handler_type):
            seen.append("with_handler")
        return expr

    @do
    def body():
        return (yield doeff_vm.WithHandler(ping_handler, _ping_program("x")))

    wrapped = doeff_vm.WithIntercept(
        observe,
        body(),
        (with_handler_type,),
        "include",
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "handled:x"
    assert seen == ["with_handler"]


@pytest.mark.asyncio
async def test_with_intercept_can_filter_doctrl_resume(parameterized_interpreter) -> None:
    seen: list[str] = []

    def observe(expr):
        if isinstance(expr, doeff_vm.Resume):
            seen.append("resume")
        return expr

    wrapped = doeff_vm.WithIntercept(
        observe,
        doeff_vm.WithHandler(ping_handler, _ping_program("x")),
        (doeff_vm.Resume,),
        "include",
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "handled:x"
    assert seen == ["resume"]


@pytest.mark.asyncio
async def test_with_intercept_no_reentrancy_same_interceptor(parameterized_interpreter) -> None:
    seen: list[str] = []

    def observe(expr):
        @do
        def effectful_observer():
            if isinstance(expr, WriterTellEffect):
                seen.append(expr.message)
                yield Tell("from-observer")
            return expr

        return effectful_observer()

    @do
    def body():
        yield Tell("body")
        return "ok"

    @do
    def main():
        return (
            yield Listen(doeff_vm.WithIntercept(observe, body(), (WriterTellEffect,), "include"))
        )

    result = await parameterized_interpreter.run_async(main())
    assert result.is_ok
    assert result.value.value == "ok"
    assert result.value.log == ["from-observer", "body"]
    assert seen == ["body"]


@pytest.mark.asyncio
async def test_with_intercept_nested_interceptors_compose(parameterized_interpreter) -> None:
    seen_outer: list[str] = []
    seen_inner: list[str] = []

    def inner(expr):
        @do
        def inner_effectful():
            if isinstance(expr, WriterTellEffect):
                seen_inner.append(expr.message)
                yield Tell("from-inner")
            return expr

        return inner_effectful()

    def outer(expr):
        if isinstance(expr, WriterTellEffect):
            seen_outer.append(expr.message)
        return expr

    @do
    def body():
        yield Tell("body")
        return "ok"

    wrapped = doeff_vm.WithIntercept(
        outer,
        doeff_vm.WithIntercept(inner, body(), (WriterTellEffect,), "include"),
        (WriterTellEffect,),
        "include",
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "ok"
    assert seen_inner == ["body"]
    assert seen_outer == ["from-inner", "body"]


@pytest.mark.asyncio
async def test_with_intercept_effect_transformation(parameterized_interpreter) -> None:
    def transform(expr):
        if isinstance(expr, WriterTellEffect):
            return Tell(f"mutated:{expr.message}")
        return expr

    @do
    def body():
        yield Tell("original")
        return "ok"

    @do
    def main():
        return (
            yield Listen(doeff_vm.WithIntercept(transform, body(), (WriterTellEffect,), "include"))
        )

    result = await parameterized_interpreter.run_async(main())
    assert result.is_ok
    assert result.value.value == "ok"
    assert result.value.log == ["mutated:original"]


@pytest.mark.asyncio
async def test_with_intercept_pure_observation_passthrough(parameterized_interpreter) -> None:
    def observe(expr):
        return expr

    @do
    def body():
        yield Tell("original")
        return "ok"

    @do
    def main():
        return (
            yield Listen(doeff_vm.WithIntercept(observe, body(), (WriterTellEffect,), "include"))
        )

    result = await parameterized_interpreter.run_async(main())
    assert result.is_ok
    assert result.value.value == "ok"
    assert result.value.log == ["original"]


@pytest.mark.asyncio
async def test_with_intercept_effectful_interceptor(parameterized_interpreter) -> None:
    def effectful(expr):
        @do
        def effectful_wrapper():
            yield Tell("side-effect")
            return expr

        return effectful_wrapper()

    @do
    def body():
        yield Tell("body")
        return "ok"

    @do
    def main():
        return (
            yield Listen(doeff_vm.WithIntercept(effectful, body(), (WriterTellEffect,), "include"))
        )

    result = await parameterized_interpreter.run_async(main())
    assert result.is_ok
    assert result.value.value == "ok"
    assert result.value.log == ["side-effect", "body"]


@pytest.mark.asyncio
async def test_with_intercept_empty_types_include_never_matches(parameterized_interpreter) -> None:
    hit_count = 0

    def observe(expr):
        nonlocal hit_count
        hit_count += 1
        return expr

    @do
    def body():
        yield Tell("body")
        return "ok"

    wrapped = doeff_vm.WithIntercept(observe, body(), (), "include")
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "ok"
    assert hit_count == 0


@pytest.mark.asyncio
async def test_with_intercept_empty_types_exclude_matches_everything(parameterized_interpreter) -> None:
    seen_types: list[str] = []

    def observe(expr):
        seen_types.append(type(expr).__name__)
        return expr

    @do
    def body():
        yield Tell("body")
        return "ok"

    wrapped = doeff_vm.WithIntercept(observe, body(), (), "exclude")
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "ok"
    assert len(seen_types) >= 1
    assert "PyTell" in seen_types


def test_with_intercept_trace_contains_interceptor_frame() -> None:
    def trace_observer(expr):
        return expr

    @do
    def body():
        yield Tell("body")
        return "ok"

    result = run(
        doeff_vm.WithIntercept(trace_observer, body(), (WriterTellEffect,), "include"),
        handlers=default_handlers(),
        trace=True,
    )
    assert result.is_ok()
    assert result.value == "ok"
    assert result.trace is not None
    assert any(
        isinstance(entry, dict) and "HandleYield(Apply)" in str(entry.get("mode", ""))
        for entry in result.trace
    )
