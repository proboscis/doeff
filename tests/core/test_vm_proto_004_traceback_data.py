from __future__ import annotations

from doeff import Gather, Program, Spawn, do
from doeff.effects import Put
from doeff.rust_vm import default_handlers, run
from doeff.traceback import attach_doeff_traceback


def test_run_result_traceback_data_is_none_on_success() -> None:
    @do
    def body() -> Program[int]:
        return 7
        yield

    result = run(body(), handlers=default_handlers())
    assert result.is_ok(), result.error
    assert result.traceback_data is None


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
    message = str(result.error)
    assert "yielded value must be EffectBase or DoExpr" in message
    assert "(type: object)" in message
    assert "from generator " in message
    assert "body" in message
    assert result.traceback_data is not None


def test_invalid_yield_in_spawned_task_preserves_traceback_data_and_context() -> None:
    class InvalidYield:
        def __repr__(self) -> str:
            return "InvalidYield()"

    @do
    def child() -> Program[object]:
        yield InvalidYield()
        return "unreachable"

    @do
    def parent() -> Program[list[object]]:
        task = yield Spawn(child())
        return (yield Gather(task))

    direct_result = run(child(), handlers=default_handlers())
    spawned_result = run(parent(), handlers=default_handlers())

    assert direct_result.is_err()
    assert spawned_result.is_err()
    assert isinstance(spawned_result.error, TypeError)
    assert spawned_result.traceback_data is not None

    message = str(spawned_result.error)
    assert "yielded value must be EffectBase or DoExpr" in message
    assert "InvalidYield" in message
    assert "InvalidYield()" in message
    assert "child" in message
    assert message == str(direct_result.error)

    doeff_tb = attach_doeff_traceback(
        spawned_result.error, traceback_data=spawned_result.traceback_data
    )
    assert doeff_tb is not None
    rendered = doeff_tb.format_default()
    assert "yield Gather(" in rendered
    assert "── in task " in rendered
    assert "child()" in rendered
    assert (
        "raise TypeError('yielded value must be EffectBase or DoExpr; got InvalidYield()"
        in rendered
    )
