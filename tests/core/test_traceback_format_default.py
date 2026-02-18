from __future__ import annotations

from typing import Protocol, cast

from doeff import Ask, Program, Put, default_handlers, do, run
from doeff.trace import HandlerStackEntry, TraceDispatch
from doeff.traceback import build_doeff_traceback


class _ErrResult(Protocol):
    error: object

    def is_err(self) -> bool: ...


def _assert_err(result: object) -> BaseException:
    err_result = cast(_ErrResult, result)
    assert err_result.is_err()
    error = err_result.error
    assert isinstance(error, BaseException)
    return error


def test_format_default_effect_repr_human_readable() -> None:
    @do
    def body() -> Program[int]:
        yield Put("counter", 1)
        raise RuntimeError("boom")
        yield

    result = run(body(), handlers=default_handlers(), store={"counter": 0}, print_doeff_trace=False)
    error = _assert_err(result)
    rendered = error.__doeff_traceback__.format_default()
    assert "Put(" in rendered
    assert "counter" in rendered
    assert "object at 0x" not in rendered


def test_format_default_hides_internal_handlers() -> None:
    @do
    def body() -> Program[int]:
        _ = yield Ask("missing_key")
        return 1

    result = run(body(), handlers=default_handlers(), print_doeff_trace=False)
    error = _assert_err(result)
    rendered = error.__doeff_traceback__.format_default()
    assert "sync_await_handler↗" not in rendered
    assert "async_await_handler↗" not in rendered


def test_format_default_resume_value_truncated_80() -> None:
    long_value = "x" * 200
    dispatch = TraceDispatch(
        dispatch_id=101,
        effect_repr='Put("key", 1)',
        handler_name="StateHandlerFactory",
        handler_kind="rust_builtin",
        handler_source_file=None,
        handler_source_line=None,
        delegation_chain=(),
        handler_stack=(HandlerStackEntry("StateHandlerFactory", "resumed"),),
        action="resumed",
        value_repr=long_value,
        exception_repr=None,
    )
    ex = RuntimeError("boom")
    rendered = build_doeff_traceback(ex, [dispatch], allow_active=True).format_default()
    resumed_line = next(line for line in rendered.splitlines() if "→ resumed with" in line)
    resumed_value = resumed_line.split("→ resumed with ", 1)[1]
    assert len(resumed_value) <= 83
    assert resumed_value.endswith("...")


def test_format_default_spawn_separator_from_payload() -> None:
    root_dispatch = TraceDispatch(
        dispatch_id=1,
        effect_repr='Gather(["t0"])',
        handler_name="SchedulerHandler",
        handler_kind="rust_builtin",
        handler_source_file=None,
        handler_source_line=None,
        delegation_chain=(),
        handler_stack=(HandlerStackEntry("SchedulerHandler", "threw"),),
        action="threw",
        value_repr=None,
        exception_repr='RuntimeError("boom")',
    )
    child_dispatch = TraceDispatch(
        dispatch_id=2,
        effect_repr='Put("x", 1)',
        handler_name="StateHandlerFactory",
        handler_kind="rust_builtin",
        handler_source_file=None,
        handler_source_line=None,
        delegation_chain=(),
        handler_stack=(HandlerStackEntry("StateHandlerFactory", "threw"),),
        action="threw",
        value_repr=None,
        exception_repr='RuntimeError("boom")',
    )
    payload = {
        "trace": [root_dispatch],
        "spawned_from": {
            "trace": [child_dispatch],
            "task_id": 9,
            "parent_task": 0,
            "spawn_site": {
                "function_name": "parent",
                "file": "tests/sample.py",
                "line": 42,
            },
        },
    }
    ex = RuntimeError("boom")
    doeff_tb = build_doeff_traceback(ex, payload, allow_active=True)
    rendered = doeff_tb.format_default()
    assert "── in task 9 (spawned at parent tests/sample.py:42) ──" in rendered


def test_existing_formats_unchanged() -> None:
    @do
    def body() -> Program[int]:
        _ = yield Ask("missing_key")
        return 1

    result = run(body(), handlers=default_handlers(), print_doeff_trace=False)
    error = _assert_err(result)
    doeff_tb = error.__doeff_traceback__
    chained = doeff_tb.format_chained()
    sectioned = doeff_tb.format_sectioned()
    short = doeff_tb.format_short()
    assert "doeff Traceback (most recent call last):" in chained
    assert "Program Stack:" in sectioned
    assert "RuntimeError" in short or "KeyError" in short or "TypeError" in short
