"""Bug: yield Try(...) inside WithHandler(_, WithIntercept(...)) breaks the
interceptor for all subsequent effects.

After a `yield Try(...)` executes, the `WithIntercept` interceptor stops
firing for all subsequent `WriterTellEffect` (slog) yields. The program
itself continues to run correctly — only the interceptor is broken.

The bug does NOT require Local or Ask — just Try + WithHandler + WithIntercept.

Minimal reproduction:
    WithHandler(passthrough,
        WithIntercept(interceptor,
            program()    # yields Try(...), then slog — interceptor misses slog
        )
    )

Expected: interceptor sees all slog effects
Actual:   interceptor sees slog BEFORE Try() but not AFTER
"""

from __future__ import annotations

import pytest

from doeff import (
    Ask,
    Effect,
    EffectGenerator,
    Local,
    Pass,
    Try,
    WithHandler,
    WithIntercept,
    WriterTellEffect,
    default_handlers,
    do,
    run,
)
from doeff import slog


@do
def _passthrough_handler(effect: Effect, k):
    yield Pass()


def _run_and_capture(program) -> tuple[object, list[str]]:
    """Run program with interceptor, return (result, captured handler names)."""
    captured: list[str] = []

    @do
    def interceptor(effect: WriterTellEffect):
        if isinstance(effect.message, dict):
            captured.append(effect.message.get("handler", "?"))
        return effect

    intercepted = WithIntercept(
        interceptor, program, types=(WriterTellEffect,), mode="include"
    )
    wrapped = WithHandler(_passthrough_handler, intercepted)
    result = run(wrapped, handlers=[*default_handlers()])
    return result, captured


def test_try_ask_in_local_breaks_intercept():
    """Core bug: Try(Ask) in Local kills interceptor for subsequent effects."""

    @do
    def stage_a() -> EffectGenerator[str]:
        attempted = yield Try(Ask("key"))
        yield slog(handler="s1a", msg="after ask")
        return "a"

    @do
    def stage_b() -> EffectGenerator[str]:
        yield slog(handler="s1b", msg="running b")
        return "b"

    @do
    def pipeline() -> EffectGenerator[str]:
        yield slog(handler="pipe", msg="start")
        a: str = yield stage_a()
        yield slog(handler="pipe", msg="a done")
        b: str = yield stage_b()
        yield slog(handler="pipe", msg="all done")
        return b

    program = Local({"key": "val"}, pipeline())
    result, captured = _run_and_capture(program)

    assert result.value == "b"
    # BUG: only ["pipe", "s1a"] captured — "pipe" (a done), "s1b", "pipe" (all done) missing
    assert "s1b" in captured, f"s1b missing from {captured}"
    assert captured.count("pipe") == 3, f"expected 3 pipe slogs, got {captured}"


def test_try_ask_without_local_works():
    """Baseline: same pattern WITHOUT Local works fine."""

    @do
    def stage_a() -> EffectGenerator[str]:
        attempted = yield Try(Ask("key"))
        yield slog(handler="s1a", msg="after ask")
        return "a"

    @do
    def stage_b() -> EffectGenerator[str]:
        yield slog(handler="s1b", msg="running b")
        return "b"

    @do
    def pipeline() -> EffectGenerator[str]:
        yield slog(handler="pipe", msg="start")
        a: str = yield stage_a()
        yield slog(handler="pipe", msg="a done")
        b: str = yield stage_b()
        yield slog(handler="pipe", msg="all done")
        return b

    # Without Local — Ask("key") will fail but Try catches it
    result, captured = _run_and_capture(pipeline())

    assert result.value == "b"
    assert "s1b" in captured, f"s1b missing from {captured}"


def test_ask_without_try_in_local_works():
    """Ask (without Try) in Local doesn't break interceptor."""

    @do
    def stage_a() -> EffectGenerator[str]:
        val = yield Ask("key")
        yield slog(handler="s1a", msg="got val")
        return val

    @do
    def stage_b() -> EffectGenerator[str]:
        yield slog(handler="s1b", msg="running b")
        return "b"

    @do
    def pipeline() -> EffectGenerator[str]:
        yield slog(handler="pipe", msg="start")
        a: str = yield stage_a()
        yield slog(handler="pipe", msg="a done")
        b: str = yield stage_b()
        yield slog(handler="pipe", msg="all done")
        return b

    program = Local({"key": "val"}, pipeline())
    result, captured = _run_and_capture(program)

    assert result.value == "b"
    assert "s1b" in captured, f"s1b missing from {captured}"


def test_try_without_ask_in_local_works():
    """Try (wrapping a pure value, not Ask) in Local doesn't break interceptor."""

    @do
    def pure_value() -> EffectGenerator[str]:
        return "hello"

    @do
    def stage_a() -> EffectGenerator[str]:
        attempted = yield Try(pure_value())
        yield slog(handler="s1a", msg="after try")
        return "a"

    @do
    def stage_b() -> EffectGenerator[str]:
        yield slog(handler="s1b", msg="running b")
        return "b"

    @do
    def pipeline() -> EffectGenerator[str]:
        yield slog(handler="pipe", msg="start")
        a: str = yield stage_a()
        yield slog(handler="pipe", msg="a done")
        b: str = yield stage_b()
        yield slog(handler="pipe", msg="all done")
        return b

    program = Local({"key": "val"}, pipeline())
    result, captured = _run_and_capture(program)

    assert result.value == "b"
    assert "s1b" in captured, f"s1b missing from {captured}"
