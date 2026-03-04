from __future__ import annotations

import warnings
from dataclasses import dataclass

from doeff import Effect, Program, do
from doeff._types_internal import EffectBase
from doeff.effects import ProgramCallStack, Put
from doeff.rust_vm import Delegate, Pass, Resume, WithHandler, default_handlers, run
from doeff.trace import TraceDispatch
from doeff.traceback import attach_doeff_traceback


@dataclass(frozen=True, kw_only=True)
class NeedsHandler(EffectBase):
    value: int


@dataclass(frozen=True, kw_only=True)
class Explode(EffectBase):
    pass


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
    @do
    def inner_handler(effect: Effect, _k):
        if isinstance(effect, NeedsHandler):
            delegated_result = yield Delegate()
            return delegated_result
        yield Pass()

    @do
    def outer_handler(effect: Effect, k):
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


def test_handler_sources_and_exception_repr_for_thrown_handler() -> None:
    @do
    def crashing_handler(effect: Effect, _k):
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
