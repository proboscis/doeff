from __future__ import annotations

import warnings
from dataclasses import dataclass

from doeff import Program, do
from doeff._types_internal import EffectBase
from doeff.effects import ProgramCallStack, ProgramTrace, Put, slog
from doeff.rust_vm import Delegate, Pass, Resume, WithHandler, default_handlers, run
from doeff.trace import TraceDispatch, TraceFrame
from doeff.traceback import attach_doeff_traceback


@dataclass(frozen=True, kw_only=True)
class Ping(EffectBase):
    value: int


@dataclass(frozen=True, kw_only=True)
class NeedsHandler(EffectBase):
    value: int


@dataclass(frozen=True, kw_only=True)
class Explode(EffectBase):
    pass


def test_program_trace_from_do_function_and_transparency_invariant() -> None:
    @do
    def program() -> Program[tuple[int, list[object]]]:
        before = yield ProgramTrace()
        yield Put("counter", 1)
        after = yield ProgramTrace()
        return len(before), after

    result = run(program(), handlers=default_handlers(), store={"counter": 0})
    assert result.is_ok(), result.error

    before_len, trace_entries = result.value
    assert before_len >= 1

    dispatches = [entry for entry in trace_entries if isinstance(entry, TraceDispatch)]
    assert dispatches, "ProgramTrace should return dispatch entries"
    assert all("ProgramTrace" not in dispatch.effect_repr for dispatch in dispatches)


def test_slog_dispatch_effect_repr_contains_payload() -> None:
    @do
    def body() -> Program[tuple[list[object], list[object]]]:
        before = yield ProgramTrace()
        yield slog(msg="validation_failed", level="warn")
        after = yield ProgramTrace()
        return before, after

    result = run(body(), handlers=default_handlers())
    assert result.is_ok(), result.error
    before_trace, after_trace = result.value

    assert isinstance(before_trace, list)
    assert isinstance(after_trace, list)
    assert len(after_trace) > len(before_trace)

    dispatches = [entry for entry in after_trace if isinstance(entry, TraceDispatch)]
    assert any(
        "validation_failed" in entry.effect_repr and "level" in entry.effect_repr
        for entry in dispatches
    ), dispatches


def test_program_trace_from_python_handler() -> None:
    def handler(effect, k):
        if isinstance(effect, Ping):
            handler_view = yield ProgramTrace()
            resumed = yield Resume(k, effect.value + 1)
            return resumed, handler_view
        yield Pass()

    @do
    def body() -> Program[int]:
        value = yield Ping(value=41)
        return value

    result = run(WithHandler(handler, body()), handlers=default_handlers())
    assert result.is_ok(), result.error

    resumed_value, handler_trace = result.value
    assert resumed_value == 42
    assert any(isinstance(entry, TraceDispatch) for entry in handler_trace)


def test_program_callstack_still_works_with_deprecation_warning() -> None:
    @do
    def body() -> Program[object]:
        stack = yield ProgramCallStack()
        return stack

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = run(body(), handlers=default_handlers())

    assert any(issubclass(item.category, DeprecationWarning) for item in caught)
    assert result.is_ok(), result.error
    assert isinstance(result.value, (tuple, list))


def test_exceptions_attach_doeff_traceback_and_rendering() -> None:
    @do
    def body() -> Program[int]:
        yield Put("x", 1)
        raise ValueError("boom")
        yield

    result = run(body(), handlers=default_handlers(), store={"x": 0})
    assert result.is_err()

    error = result.error
    traceback_data = result.traceback_data
    assert traceback_data is not None
    assert type(traceback_data).__name__ == "DoeffTracebackData"
    assert isinstance(traceback_data.entries, list)
    assert not hasattr(error, "__doeff_traceback_data__")
    assert not hasattr(error, "__doeff_traceback__")

    doeff_tb = attach_doeff_traceback(error, traceback_data=traceback_data)
    assert doeff_tb is not None
    default = doeff_tb.format_default()
    chained = doeff_tb.format_chained()
    sectioned = doeff_tb.format_sectioned()
    short = doeff_tb.format_short()

    assert "doeff Traceback (most recent call last):" in default
    assert "doeff Traceback (most recent call last):" in chained
    assert "Program Stack:" in sectioned
    assert "ValueError: boom" in short

    assert "RunResult status: err" in result.display(verbose=False)
    assert "RunResult status: err" in result.display(verbose=True)


def test_delegation_chain_routes_to_outer_handler() -> None:
    def inner_handler(effect, _k):
        if isinstance(effect, NeedsHandler):
            delegated_result = yield Delegate()
            return delegated_result
        yield Pass()

    def outer_handler(effect, k):
        if isinstance(effect, NeedsHandler):
            return (yield Resume(k, effect.value))
        yield Pass()

    @do
    def body() -> Program[int]:
        result = yield NeedsHandler(value=7)
        return result

    wrapped = WithHandler(outer_handler, WithHandler(inner_handler, body()))
    result = run(wrapped, handlers=default_handlers())
    assert result.is_ok(), result.error
    assert result.value == 7


def test_recursive_frames_preserve_frame_id_and_args_repr() -> None:
    @do
    def recursive(n: int) -> Program[list[object]]:
        if n <= 0:
            return (yield ProgramTrace())
        return (yield recursive(n - 1))

    result = run(recursive(3), handlers=default_handlers())
    assert result.is_ok(), result.error

    recursive_frames = [
        entry
        for entry in result.value
        if isinstance(entry, TraceFrame) and entry.function_name == "recursive"
    ]
    assert recursive_frames

    first_frame_by_id: dict[int, TraceFrame] = {}
    for frame in recursive_frames:
        first_frame_by_id.setdefault(frame.frame_id, frame)

    assert len(first_frame_by_id) >= 4
    assert all(
        frame.args_repr is None or "n=" in frame.args_repr or "args=" in frame.args_repr
        for frame in first_frame_by_id.values()
    )


def test_handler_sources_and_exception_repr_for_thrown_handler() -> None:
    def crashing_handler(effect, _k):
        if isinstance(effect, Explode):
            raise RuntimeError("handler exploded")
        yield Pass()

    @do
    def body() -> Program[int]:
        yield Put("x", 1)
        yield Explode()
        return 0

    wrapped = WithHandler(crashing_handler, body())
    result = run(wrapped, handlers=default_handlers(), store={"x": 0})
    assert result.is_err()

    traceback_data = result.traceback_data
    assert traceback_data is not None
    trace_entries = traceback_data.entries
    dispatches = [entry for entry in trace_entries if isinstance(entry, TraceDispatch)]
    assert dispatches

    python_throw = next(
        dispatch
        for dispatch in dispatches
        if "Explode" in dispatch.effect_repr and dispatch.action == "threw"
    )
    assert python_throw.handler_kind == "python"
    assert python_throw.handler_source_file is not None
    assert python_throw.handler_source_line is not None
    assert python_throw.exception_repr is not None
    assert "handler exploded" in python_throw.exception_repr

    rust_builtin = next(dispatch for dispatch in dispatches if "Put" in dispatch.effect_repr)
    assert rust_builtin.handler_kind == "rust_builtin"
    assert rust_builtin.handler_source_file is None
    assert rust_builtin.handler_source_line is None
