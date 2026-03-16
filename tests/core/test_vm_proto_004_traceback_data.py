from __future__ import annotations

from dataclasses import dataclass

import doeff_vm

from doeff import Effect, Pass, Program, Try, WithHandler, do
from doeff.effects import Put
from doeff.effects.base import EffectBase
from doeff.rust_vm import default_handlers, run


def test_run_result_traceback_data_is_none_on_success() -> None:
    @do
    def body() -> Program[int]:
        return 7
        yield

    result = run(body(), handlers=default_handlers())
    assert result.is_ok(), result.error
    assert result.traceback_data is None
    assert result.last_active_chain == []
    assert result.early_terminated is False


def test_run_result_exposes_typed_traceback_data_without_exception_dunders() -> None:
    @do
    def body() -> Program[int]:
        yield Put("x", 1)
        raise ValueError("boom")
        yield

    result = run(body(), handlers=default_handlers(), store={"x": 0})
    assert result.is_err()

    traceback_data = result.traceback_data
    assert traceback_data is not None
    assert type(traceback_data).__name__ == "DoeffTracebackData"
    assert isinstance(traceback_data.entries, list)
    assert traceback_data.entries

    error = result.error
    assert isinstance(error, ValueError)
    assert not hasattr(error, "__doeff_traceback_data__")
    assert not hasattr(error, "__doeff_traceback__")


def test_invalid_top_level_yield_returns_err_run_result_with_traceback_data() -> None:
    @do
    def body() -> Program[object]:
        yield object()
        return "unreachable"

    result = run(body(), handlers=default_handlers())

    assert result.is_err()
    assert isinstance(result.error, TypeError)
    assert str(result.error).startswith("yielded value must be EffectBase or DoExpr, got ")
    assert "(type: object)" in str(result.error)
    assert "<object object at " in str(result.error)
    assert result.traceback_data is not None
    assert result.last_active_chain == result.traceback_data.active_chain


def test_run_result_last_active_chain_tracks_caught_handler_protocol_error() -> None:
    @dataclass(frozen=True, kw_only=True)
    class ProbeEffect(EffectBase):
        pass

    @do
    def bad_handler(effect: Effect, _k: object):
        if isinstance(effect, ProbeEffect):
            return "bad-return"
        yield Pass()

    @do
    def inner() -> Program[None]:
        yield ProbeEffect()

    @do
    def body():
        return (yield Try(WithHandler(bad_handler, inner())))

    result = run(body(), handlers=default_handlers())

    assert result.is_ok(), result.error
    assert result.traceback_data is None
    assert result.value.is_err()
    assert result.early_terminated is True
    assert result.last_active_chain

    effect_entries = [
        entry
        for entry in result.last_active_chain
        if isinstance(entry, dict) and entry.get("kind") == "effect_yield"
    ]
    assert effect_entries

    assert any(entry["result"]["kind"] == "threw" for entry in effect_entries)
    assert any(
        "handler returned without consuming continuation" in entry["result"]["exception_repr"]
        for entry in effect_entries
    )
    assert any(
        str(handler["handler_name"]).endswith("bad_handler") and handler["status"] == "threw"
        for entry in effect_entries
        for handler in entry["handler_stack"]
    )


def test_build_run_result_marks_early_terminated_when_root_program_is_unfinished() -> None:
    vm = doeff_vm.PyVM()

    @do
    def body() -> Program[int]:
        value = yield Program.pure(7)
        return value

    vm.start_program(body())

    result = None
    for _ in range(4):
        step = vm.step_once()
        assert step[0] in {"continue", "done"}
        result = vm.build_run_result(123)
        if result.early_terminated:
            break

    assert result is not None
    assert result.is_ok(), result.error
    assert result.value == 123
    assert result.early_terminated is True
