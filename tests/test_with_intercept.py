from __future__ import annotations

from dataclasses import dataclass

import doeff_vm
import pytest

from doeff import (
    EffectBase,
    Listen,
    Tell,
    WriterTellEffect,
    default_handlers,
    do,
    run,
    with_intercept,
)


@dataclass(frozen=True)
class Ping(EffectBase):
    label: str


@dataclass(frozen=True)
class Log(EffectBase):
    msg: str


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


def handler_a(effect, k):
    if isinstance(effect, Ping):
        yield Tell(f"handler_a:{effect.label}")
        yield Log(msg=f"log_from_a:{effect.label}")
        return (yield doeff_vm.Resume(k, f"handled:{effect.label}"))
    delegated = yield doeff_vm.Delegate()
    return (yield doeff_vm.Resume(k, delegated))


def handler_b(effect, k):
    if isinstance(effect, Log):
        yield Tell(f"handler_b_saw:{effect.msg}")
        return (yield doeff_vm.Resume(k, None))
    delegated = yield doeff_vm.Delegate()
    return (yield doeff_vm.Resume(k, delegated))


def always_delegate(effect, k):
    delegated = yield doeff_vm.Delegate()
    return (yield doeff_vm.Resume(k, delegated))


def _run_result_is_ok(result) -> bool:
    is_ok = getattr(result, "is_ok", None)
    if callable(is_ok):
        return bool(is_ok())
    return bool(is_ok)


def _run_result_is_err(result) -> bool:
    is_err = getattr(result, "is_err", None)
    if callable(is_err):
        return bool(is_err())
    return bool(is_err)


def _with_intercept(f, expr, types=(), mode="include"):
    return with_intercept(f, expr, types=types, mode=mode)


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

    wrapped = _with_intercept(observe, body(), (WriterTellEffect,), "include")
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "ok"
    assert seen == ["user"]


@pytest.mark.asyncio
async def test_with_intercept_raw_two_arg_ir(parameterized_interpreter) -> None:
    seen: list[str] = []

    def observe(expr):
        if isinstance(expr, WriterTellEffect):
            seen.append(expr.message)
        return doeff_vm.Pure(expr)

    @do
    def body():
        yield Tell("raw")
        return "ok"

    wrapped = doeff_vm.WithIntercept(observe, body())
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "ok"
    assert seen == ["raw"]


@pytest.mark.asyncio
async def test_with_intercept_observes_handler_tell_cross_cutting(
    parameterized_interpreter,
) -> None:
    seen: list[str] = []

    def observe(expr):
        if isinstance(expr, WriterTellEffect):
            seen.append(expr.message)
        return expr

    wrapped = _with_intercept(
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
        _with_intercept(observe, body(), (WriterTellEffect,), "include"),
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
        _with_intercept(observe, body(), (WriterTellEffect,), "exclude"),
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

    wrapped = _with_intercept(
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

    wrapped = _with_intercept(
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
        return (yield Listen(_with_intercept(observe, body(), (WriterTellEffect,), "include")))

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

    wrapped = _with_intercept(
        outer,
        _with_intercept(inner, body(), (WriterTellEffect,), "include"),
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
        return (yield Listen(_with_intercept(transform, body(), (WriterTellEffect,), "include")))

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
        return (yield Listen(_with_intercept(observe, body(), (WriterTellEffect,), "include")))

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
        return (yield Listen(_with_intercept(effectful, body(), (WriterTellEffect,), "include")))

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

    wrapped = _with_intercept(observe, body(), (), "include")
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "ok"
    assert hit_count == 0


@pytest.mark.asyncio
async def test_with_intercept_empty_types_exclude_matches_everything(
    parameterized_interpreter,
) -> None:
    seen_types: list[str] = []

    def observe(expr):
        seen_types.append(type(expr).__name__)
        return expr

    @do
    def body():
        yield Tell("body")
        return "ok"

    wrapped = _with_intercept(observe, body(), (), "exclude")
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
        _with_intercept(trace_observer, body(), (WriterTellEffect,), "include"),
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


@pytest.mark.asyncio
async def test_with_intercept_deep_handler_nesting_cross_cutting(parameterized_interpreter) -> None:
    seen: list[str] = []

    def observe(expr):
        if isinstance(expr, WriterTellEffect):
            seen.append(expr.message)
        return expr

    wrapped = _with_intercept(
        observe,
        doeff_vm.WithHandler(handler_b, doeff_vm.WithHandler(handler_a, _ping_program("deep"))),
        (WriterTellEffect,),
        "include",
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "handled:deep"
    assert "handler_a:deep" in seen
    assert "handler_b_saw:log_from_a:deep" in seen


@pytest.mark.asyncio
async def test_with_intercept_observes_delegate_path(parameterized_interpreter) -> None:
    seen_types: list[str] = []

    def observe(expr):
        seen_types.append(type(expr).__name__)
        return expr

    wrapped = _with_intercept(
        observe,
        doeff_vm.WithHandler(
            ping_handler, doeff_vm.WithHandler(always_delegate, _ping_program("x"))
        ),
        (),
        "exclude",
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "handled:x"
    assert any("Delegate" in name for name in seen_types)


@pytest.mark.asyncio
async def test_with_intercept_delegate_resume_no_leak(parameterized_interpreter) -> None:
    """After delegate-resume, outer handler yields should not be intercepted."""
    seen: list[str] = []

    def observe(expr):
        if isinstance(expr, WriterTellEffect):
            seen.append(expr.message)
        return expr

    def inner_handler(effect, k):
        if isinstance(effect, Ping):
            return (yield doeff_vm.Resume(k, f"inner:{effect.label}"))
        delegated = yield doeff_vm.Delegate()
        return (yield doeff_vm.Resume(k, delegated))

    def outer_handler(effect, k):
        if isinstance(effect, Ping):
            delegated = yield doeff_vm.Delegate()
            yield Tell(f"outer-after-resume:{delegated}")
            return (yield doeff_vm.Resume(k, delegated))
        delegated = yield doeff_vm.Delegate()
        return (yield doeff_vm.Resume(k, delegated))

    @do
    def body():
        return (yield Ping("x"))

    wrapped = doeff_vm.WithHandler(
        outer_handler,
        _with_intercept(
            observe,
            doeff_vm.WithHandler(inner_handler, body()),
            (WriterTellEffect,),
            "include",
        ),
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert "outer-after-resume:inner:x" not in seen


@pytest.mark.asyncio
async def test_with_intercept_withhandler_outside_scope(parameterized_interpreter) -> None:
    seen: list[str] = []

    def observe(expr):
        if isinstance(expr, WriterTellEffect):
            seen.append(expr.message)
        return expr

    @do
    def body():
        yield Tell("from_program")
        _ = yield Ping("x")
        return "done"

    wrapped = doeff_vm.WithHandler(
        ping_with_tell_handler,
        _with_intercept(observe, body(), (WriterTellEffect,), "include"),
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "done"
    assert "from_program" in seen
    assert "handler:x" not in seen


@pytest.mark.asyncio
async def test_with_intercept_nested_filters_match_spec_example(parameterized_interpreter) -> None:
    f1_seen: list[str] = []
    f2_seen: list[str] = []

    def f1(expr):
        if isinstance(expr, WriterTellEffect):
            f1_seen.append(expr.message)
        return expr

    def f2(expr):
        if isinstance(expr, Ping):
            f2_seen.append(expr.label)
        return expr

    @do
    def body():
        yield Tell("spec_tell")
        _ = yield Ping("spec_ping")
        return "done"

    wrapped = doeff_vm.WithHandler(
        ping_handler,
        _with_intercept(
            f1,
            _with_intercept(f2, body(), (Ping,), "include"),
            (WriterTellEffect,),
            "include",
        ),
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "done"
    assert f1_seen == ["spec_tell"]
    assert f2_seen == ["spec_ping"]


@pytest.mark.asyncio
async def test_with_intercept_long_sequence_ordered_observation(parameterized_interpreter) -> None:
    seen: list[str] = []

    def observe(expr):
        if isinstance(expr, WriterTellEffect):
            seen.append(expr.message)
        return expr

    @do
    def chatty_program():
        yield Tell("first")
        yield Tell("second")
        _ = yield Ping("mid")
        yield Tell("third")
        yield Tell("fourth")
        return "done"

    wrapped = doeff_vm.WithHandler(
        ping_handler,
        _with_intercept(observe, chatty_program(), (WriterTellEffect,), "include"),
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "done"
    assert seen == ["first", "second", "third", "fourth"]


@pytest.mark.asyncio
async def test_with_intercept_observer_raises_propagates_and_cleans_state(
    parameterized_interpreter,
) -> None:
    def bad_observer(expr):
        raise RuntimeError("interceptor broke")

    @do
    def body():
        yield Tell("boom")
        return "ok"

    wrapped = _with_intercept(bad_observer, body(), (WriterTellEffect,), "include")
    result = await parameterized_interpreter.run_async(wrapped)
    assert _run_result_is_err(result)
    assert isinstance(result.error, RuntimeError)
    assert "interceptor broke" in str(result.error)

    fire_count = 0

    def observe_after(expr):
        nonlocal fire_count
        if isinstance(expr, WriterTellEffect):
            fire_count += 1
        return expr

    @do
    def after():
        yield Tell("after")
        return "after-ok"

    after_result = await parameterized_interpreter.run_async(
        _with_intercept(observe_after, after(), (WriterTellEffect,), "include")
    )
    assert _run_result_is_ok(after_result)
    assert after_result.value == "after-ok"
    assert fire_count == 1


@pytest.mark.asyncio
async def test_with_intercept_program_error_propagates_after_prior_observation(
    parameterized_interpreter,
) -> None:
    seen: list[str] = []

    def observe(expr):
        if isinstance(expr, WriterTellEffect):
            seen.append(expr.message)
        return expr

    @do
    def failing_program():
        yield Tell("before_error")
        raise ValueError("program failed")

    wrapped = _with_intercept(observe, failing_program(), (WriterTellEffect,), "include")
    result = await parameterized_interpreter.run_async(wrapped)
    assert _run_result_is_err(result)
    assert isinstance(result.error, ValueError)
    assert "program failed" in str(result.error)
    assert seen == ["before_error"]
