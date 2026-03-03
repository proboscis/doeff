from __future__ import annotations

from dataclasses import dataclass

import doeff_vm
import pytest

from doeff import (
    Effect,
    EffectBase,
    Local,
    Tell,
    Try,
    WithIntercept,
    WriterTellEffect,
    do,
)


@pytest.mark.asyncio
async def test_with_intercept_preserved_through_local(parameterized_interpreter) -> None:
    seen: list[str] = []

    @do
    def observe(effect: Effect):
        if isinstance(effect, WriterTellEffect):
            seen.append(effect.message)
        return effect

    @do
    def body():
        yield Tell("from-local")
        return "ok"

    program = WithIntercept(
        observe,
        Local({"scope": "value"}, body()),
        (WriterTellEffect,),
        "include",
    )
    result = await parameterized_interpreter.run_async(program)
    assert result.is_ok
    assert result.value == "ok"
    assert seen == ["from-local"]


@pytest.mark.asyncio
async def test_with_intercept_preserved_through_try(parameterized_interpreter) -> None:
    seen: list[str] = []

    @do
    def observe(effect: Effect):
        if isinstance(effect, WriterTellEffect):
            seen.append(effect.message)
        return effect

    @do
    def body():
        yield Tell("before-error")
        raise ValueError("boom")

    program = WithIntercept(observe, Try(body()), (WriterTellEffect,), "include")
    result = await parameterized_interpreter.run_async(program)
    assert result.is_ok
    assert result.value.is_err()
    assert isinstance(result.value.error, ValueError)
    assert seen == ["before-error"]


@pytest.mark.asyncio
async def test_with_intercept_preserved_through_nested_local(parameterized_interpreter) -> None:
    seen: list[str] = []

    @do
    def observe(effect: Effect):
        if isinstance(effect, WriterTellEffect):
            seen.append(effect.message)
        return effect

    @do
    def body():
        yield Tell("nested-local")
        return "ok"

    program = WithIntercept(
        observe,
        Local({"a": "outer"}, Local({"b": "inner"}, body())),
        (WriterTellEffect,),
        "include",
    )
    result = await parameterized_interpreter.run_async(program)
    assert result.is_ok
    assert result.value == "ok"
    assert seen == ["nested-local"]


@pytest.mark.asyncio
@pytest.mark.parametrize("w_outside_handler", [True, False])
async def test_with_intercept_and_handler_interleaving_with_local(
    parameterized_interpreter,
    w_outside_handler: bool,
) -> None:
    seen: list[str] = []

    @dataclass(frozen=True)
    class Ping(EffectBase):
        label: str

    @do
    def observe(effect: Effect):
        if isinstance(effect, WriterTellEffect):
            seen.append(effect.message)
        return effect

    @do
    def ping_handler(effect: Effect, k):
        if not isinstance(effect, Ping):
            delegated = yield doeff_vm.Delegate()
            return (yield doeff_vm.Resume(k, delegated))
        yield Tell(f"handler:{effect.label}")
        return (yield doeff_vm.Resume(k, f"handled:{effect.label}"))

    @do
    def body():
        yield Tell("body-log")
        return (yield Ping(label="x"))

    local_body = Local({"scope": "value"}, body())
    intercepted = WithIntercept(observe, local_body, (WriterTellEffect,), "include")
    if w_outside_handler:
        program = WithIntercept(
            observe,
            doeff_vm.WithHandler(ping_handler, local_body),
            (WriterTellEffect,),
            "include",
        )
    else:
        program = doeff_vm.WithHandler(ping_handler, intercepted)

    result = await parameterized_interpreter.run_async(program)
    assert result.is_ok
    assert result.value == "handled:x"
    assert "body-log" in seen
    assert "handler:x" in seen


@pytest.mark.asyncio
async def test_multiple_interceptors_preserved_through_local(parameterized_interpreter) -> None:
    outer_seen: list[str] = []
    inner_seen: list[str] = []

    @do
    def outer(effect: Effect):
        if isinstance(effect, WriterTellEffect):
            outer_seen.append(effect.message)
        return effect

    @do
    def inner(effect: Effect):
        if isinstance(effect, WriterTellEffect):
            inner_seen.append(effect.message)
        return effect

    @do
    def body():
        yield Tell("multi")
        return "ok"

    program = WithIntercept(
        outer,
        WithIntercept(
            inner,
            Local({"scope": "value"}, body()),
            (WriterTellEffect,),
            "include",
        ),
        (WriterTellEffect,),
        "include",
    )
    result = await parameterized_interpreter.run_async(program)
    assert result.is_ok
    assert result.value == "ok"
    assert inner_seen == ["multi"]
    assert outer_seen == ["multi"]
