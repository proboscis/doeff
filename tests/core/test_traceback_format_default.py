from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path

from doeff import Ask, Delegate, EffectBase, Program, Resume, WithHandler, default_handlers, do, run
from doeff.effects import Put
from doeff.effects.gather import Gather
from doeff.effects.spawn import Spawn
from doeff.traceback import attach_doeff_traceback, build_doeff_traceback


def _tb(
    active_chain: list[dict[str, object]],
    trace: list[dict[str, object]] | None = None,
) -> object:
    return build_doeff_traceback(
        ValueError("boom"),
        trace or [],
        active_chain,
    )


def _tb_from_run_result(result: object) -> object:
    doeff_tb = attach_doeff_traceback(
        result.error,
        traceback_data=getattr(result, "traceback_data", None),
    )
    assert doeff_tb is not None
    return doeff_tb


def _line_of(function: object, needle: str) -> int:
    lines, start = inspect.getsourcelines(function)
    for offset, line in enumerate(lines):
        if needle in line:
            return start + offset
    raise AssertionError(f"failed to find {needle!r} in source")


def _render_single_delegated_handler(handler_name: str) -> str:
    tb = _tb(
        [
            {
                "kind": "effect_yield",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 7,
                "effect_repr": "Ask('k')",
                "handler_stack": [
                    {
                        "handler_name": handler_name,
                        "handler_kind": "python",
                        "status": "delegated",
                    },
                    {
                        "handler_name": "user_handler",
                        "handler_kind": "python",
                        "status": "resumed",
                    },
                ],
                "result": {"kind": "resumed", "value_repr": "1"},
            },
            {
                "kind": "exception_site",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 8,
                "exception_type": "RuntimeError",
                "message": "boom",
            },
        ]
    )
    return tb.format_default()


def test_format_default_shows_sync_await_handler_when_delegated() -> None:
    rendered = _render_single_delegated_handler("sync_await_handler")
    assert "sync_await_handler↗" in rendered


def test_format_default_shows_async_await_handler_when_delegated() -> None:
    rendered = _render_single_delegated_handler("async_await_handler")
    assert "async_await_handler↗" in rendered


def test_format_default_shows_rust_await_handler_when_delegated() -> None:
    rendered = _render_single_delegated_handler("AwaitHandler")
    assert "AwaitHandler↗" in rendered


def test_format_default_shows_every_handler_and_status_marker() -> None:
    tb = _tb(
        [
            {
                "kind": "effect_yield",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 12,
                "effect_repr": "Ping()",
                "handler_stack": [
                    {"handler_name": "h_active", "handler_kind": "python", "status": "active"},
                    {"handler_name": "h_pending", "handler_kind": "python", "status": "pending"},
                    {
                        "handler_name": "h_delegated",
                        "handler_kind": "python",
                        "status": "delegated",
                    },
                    {"handler_name": "h_resumed", "handler_kind": "python", "status": "resumed"},
                    {
                        "handler_name": "h_transferred",
                        "handler_kind": "python",
                        "status": "transferred",
                    },
                    {"handler_name": "h_returned", "handler_kind": "python", "status": "returned"},
                    {"handler_name": "h_threw", "handler_kind": "python", "status": "threw"},
                ],
                "result": {
                    "kind": "threw",
                    "handler_name": "h_threw",
                    "exception_repr": "RuntimeError('boom')",
                },
            },
            {
                "kind": "exception_site",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 13,
                "exception_type": "RuntimeError",
                "message": "boom",
            },
        ]
    )

    rendered = tb.format_default()
    assert (
        "[h_active⚡ > h_pending· > h_delegated↗ > h_resumed✓ > "
        "h_transferred⇢ > h_returned✓ > h_threw✗]"
    ) in rendered


def test_format_default_program_yield() -> None:
    tb = _tb(
        [
            {
                "kind": "program_yield",
                "function_name": "outer",
                "source_file": "program.py",
                "source_line": 10,
                "sub_program_repr": "inner()",
            },
            {
                "kind": "exception_site",
                "function_name": "inner",
                "source_file": "program.py",
                "source_line": 20,
                "exception_type": "ValueError",
                "message": "boom",
            },
        ]
    )

    rendered = tb.format_default()
    assert "outer()  program.py:10" in rendered
    assert "yield inner()" in rendered


def test_format_default_effect_yield_with_markers() -> None:
    tb = _tb(
        [
            {
                "kind": "effect_yield",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 12,
                "effect_repr": 'Put("key", 1)',
                "handler_stack": [
                    {"handler_name": "h1", "handler_kind": "python", "status": "delegated"},
                    {"handler_name": "h2", "handler_kind": "python", "status": "resumed"},
                ],
                "result": {"kind": "resumed", "value_repr": "None"},
            },
            {
                "kind": "exception_site",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 13,
                "exception_type": "ValueError",
                "message": "boom",
            },
        ]
    )

    rendered = tb.format_default()
    assert "[h1↗ > h2✓]" in rendered
    assert "→ resumed with None" in rendered


def test_format_default_handler_stack_same() -> None:
    stack = [
        {"handler_name": "h1", "handler_kind": "python", "status": "delegated"},
        {"handler_name": "h2", "handler_kind": "python", "status": "resumed"},
    ]
    tb = _tb(
        [
            {
                "kind": "effect_yield",
                "function_name": "one",
                "source_file": "program.py",
                "source_line": 1,
                "effect_repr": "Ping()",
                "handler_stack": stack,
                "result": {"kind": "resumed", "value_repr": "1"},
            },
            {
                "kind": "effect_yield",
                "function_name": "two",
                "source_file": "program.py",
                "source_line": 2,
                "effect_repr": "Ping()",
                "handler_stack": stack,
                "result": {"kind": "resumed", "value_repr": "2"},
            },
            {
                "kind": "exception_site",
                "function_name": "two",
                "source_file": "program.py",
                "source_line": 3,
                "exception_type": "ValueError",
                "message": "boom",
            },
        ]
    )

    rendered = tb.format_default()
    stack_line = "[h1↗ > h2✓]"
    assert rendered.count(stack_line) == 2
    assert "[same]" not in rendered


def test_format_default_keeps_duplicate_handler_names_from_vm_chain() -> None:
    def same_name_handler(_effect: object, _k: object):
        yield Delegate()

    @do
    def body() -> Program[None]:
        yield Put("k", 1)
        raise ValueError("boom")

    result = run(
        WithHandler(same_name_handler, WithHandler(same_name_handler, body())),
        handlers=default_handlers(),
        store={"k": 0},
    )
    assert result.is_err()

    rendered = _tb_from_run_result(result).format_default()
    handler_stack_line = next(
        line.strip()
        for line in rendered.splitlines()
        if line.strip().startswith("[same_name_handler")
    )
    assert handler_stack_line.count("same_name_handler↗") == 2


def test_format_default_duplicate_name_throw_marks_correct_handler() -> None:
    def _mk_handler(label: str):
        def handler(effect: object, k: object):
            if getattr(effect, "key", None) == "x":
                _ = yield Resume(k, f"{label}:resumed")
                raise RuntimeError(f"{label}:boom")
            yield Delegate()

        return handler

    inner = _mk_handler("inner")
    outer = _mk_handler("outer")

    @do
    def body() -> Program[str]:
        _ = yield Ask("x")
        return "done"

    result = run(
        WithHandler(outer, WithHandler(inner, body())),
        handlers=default_handlers(),
        env={"x": 1},
    )
    assert result.is_err()

    rendered = _tb_from_run_result(result).format_default()
    stack_line = next(
        line.strip() for line in rendered.splitlines() if line.strip().startswith("[handler")
    )
    assert stack_line.startswith("[handler✗ > handler·")
    assert "inner:boom" in rendered


def test_format_default_renders_all_handlers() -> None:
    tb = _tb(
        [
            {
                "kind": "effect_yield",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 7,
                "effect_repr": "Ask('k')",
                "handler_stack": [
                    {
                        "handler_name": "sync_await_handler",
                        "handler_kind": "python",
                        "status": "delegated",
                    },
                    {
                        "handler_name": "user_handler",
                        "handler_kind": "python",
                        "status": "threw",
                    },
                ],
                "result": {
                    "kind": "threw",
                    "handler_name": "user_handler",
                    "exception_repr": "RuntimeError('boom')",
                },
            },
            {
                "kind": "exception_site",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 8,
                "exception_type": "RuntimeError",
                "message": "boom",
            },
        ]
    )

    rendered = tb.format_default()
    assert "sync_await_handler↗" in rendered
    assert "user_handler" in rendered


def test_format_default_resume_value_truncated_80() -> None:
    long_value = "x" * 120
    tb = _tb(
        [
            {
                "kind": "effect_yield",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 7,
                "effect_repr": "Ping()",
                "handler_stack": [
                    {"handler_name": "h", "handler_kind": "python", "status": "resumed"}
                ],
                "result": {"kind": "resumed", "value_repr": long_value},
            },
            {
                "kind": "exception_site",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 8,
                "exception_type": "RuntimeError",
                "message": "boom",
            },
        ]
    )

    rendered = tb.format_default()
    assert "..." in rendered
    assert long_value not in rendered


def test_format_default_handler_throws() -> None:
    tb = _tb(
        [
            {
                "kind": "effect_yield",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 5,
                "effect_repr": "Boom()",
                "handler_stack": [
                    {"handler_name": "h", "handler_kind": "python", "status": "threw"}
                ],
                "result": {
                    "kind": "threw",
                    "handler_name": "h",
                    "exception_repr": "RuntimeError('handler exploded')",
                },
            },
            {
                "kind": "exception_site",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 6,
                "exception_type": "RuntimeError",
                "message": "handler exploded",
            },
        ]
    )

    rendered = tb.format_default()
    assert "✗ h raised RuntimeError('handler exploded')" in rendered


def test_format_default_transfer_inline() -> None:
    tb = _tb(
        [
            {
                "kind": "effect_yield",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 5,
                "effect_repr": "Gather(task)",
                "handler_stack": [
                    {
                        "handler_name": "SchedulerHandler",
                        "handler_kind": "rust_builtin",
                        "status": "transferred",
                    }
                ],
                "result": {
                    "kind": "transferred",
                    "handler_name": "SchedulerHandler",
                    "target_repr": "child() child.py:12",
                },
            },
            {
                "kind": "exception_site",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 6,
                "exception_type": "RuntimeError",
                "message": "boom",
            },
        ]
    )

    rendered = tb.format_default()
    assert "⇢ SchedulerHandler transferred to child() child.py:12" in rendered


def test_format_default_spawn_separator() -> None:
    tb = _tb(
        [
            {
                "kind": "spawn_boundary",
                "task_id": 4,
                "parent_task": 0,
                "spawn_site": {
                    "function_name": "parent",
                    "source_file": "parent.py",
                    "source_line": 22,
                },
            },
            {
                "kind": "exception_site",
                "function_name": "child",
                "source_file": "child.py",
                "source_line": 3,
                "exception_type": "ValueError",
                "message": "boom",
            },
        ]
    )

    rendered = tb.format_default()
    assert "── in task 4 (spawned at parent() parent.py:22) ──" in rendered


def test_format_default_nested_spawn() -> None:
    tb = _tb(
        [
            {"kind": "spawn_boundary", "task_id": 1, "parent_task": 0, "spawn_site": None},
            {"kind": "spawn_boundary", "task_id": 2, "parent_task": 1, "spawn_site": None},
            {
                "kind": "exception_site",
                "function_name": "leaf",
                "source_file": "leaf.py",
                "source_line": 5,
                "exception_type": "ValueError",
                "message": "boom",
            },
        ]
    )

    rendered = tb.format_default()
    assert rendered.count("── in task ") == 2


def test_format_default_effect_repr_human_readable() -> None:
    tb = _tb(
        [
            {
                "kind": "effect_yield",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 5,
                "effect_repr": 'Put("key", 1)',
                "handler_stack": [
                    {
                        "handler_name": "state",
                        "handler_kind": "rust_builtin",
                        "status": "active",
                    }
                ],
                "result": {"kind": "active"},
            },
            {
                "kind": "exception_site",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 6,
                "exception_type": "ValueError",
                "message": "boom",
            },
        ]
    )

    assert 'yield Put("key", 1)' in tb.format_default()


def test_existing_formats_unchanged() -> None:
    trace = [
        {
            "kind": "frame",
            "frame_id": 1,
            "function_name": "program",
            "source_file": "app.py",
            "source_line": 10,
            "args_repr": None,
        },
        {
            "kind": "dispatch",
            "dispatch_id": 2,
            "effect_repr": 'Put("x", 1)',
            "handler_name": "state",
            "handler_kind": "rust_builtin",
            "handler_source_file": None,
            "handler_source_line": None,
            "delegation_chain": [],
            "action": "returned",
            "value_repr": "None",
            "exception_repr": None,
        },
    ]
    tb = _tb(
        [
            {
                "kind": "exception_site",
                "function_name": "program",
                "source_file": "app.py",
                "source_line": 12,
                "exception_type": "ValueError",
                "message": "boom",
            }
        ],
        trace=trace,
    )

    assert "doeff Traceback (most recent call last):" in tb.format_chained()
    assert "Program Stack:" in tb.format_sectioned()
    assert "ValueError: boom" in tb.format_short()


def test_format_default_ignores_historical_trace_rows_when_active_chain_present() -> None:
    trace = [
        {
            "kind": "frame",
            "frame_id": 1,
            "function_name": "stale_frame",
            "source_file": "stale.py",
            "source_line": 10,
            "args_repr": None,
        },
        {
            "kind": "dispatch",
            "dispatch_id": 77,
            "effect_repr": "HistoricalEffect()",
            "handler_name": "stale_handler",
            "handler_kind": "rust_builtin",
            "handler_source_file": None,
            "handler_source_line": None,
            "delegation_chain": [],
            "action": "threw",
            "value_repr": None,
            "exception_repr": "RuntimeError('old boom')",
        },
    ]
    active_chain = [
        {
            "kind": "effect_yield",
            "function_name": "live_frame",
            "source_file": "live.py",
            "source_line": 42,
            "effect_repr": "LiveEffect()",
            "handler_stack": [
                {"handler_name": "live_handler", "handler_kind": "python", "status": "active"}
            ],
            "result": {"kind": "active"},
        },
        {
            "kind": "exception_site",
            "function_name": "live_frame",
            "source_file": "live.py",
            "source_line": 43,
            "exception_type": "ValueError",
            "message": "boom",
        },
    ]

    tb = _tb(active_chain, trace=trace)
    rendered = tb.format_default()
    assert "live_frame()  live.py:42" in rendered
    assert "LiveEffect()" in rendered
    assert "stale_frame" not in rendered
    assert "HistoricalEffect()" not in rendered
    assert "stale_handler" not in rendered


@dataclass(frozen=True, kw_only=True)
class Boom(EffectBase):
    pass


def test_format_default_shows_effect_yield_on_handler_throw() -> None:
    def crash_handler(effect: object, _k: object):
        if isinstance(effect, Boom):
            raise RuntimeError("handler exploded")
        yield Delegate()

    @do
    def body() -> Program[int]:
        yield Boom()
        return 1

    result = run(WithHandler(crash_handler, body()), handlers=default_handlers())
    assert result.is_err()

    rendered = _tb_from_run_result(result).format_default()
    assert "yield Boom" in rendered
    assert "crash_handler✗" in rendered
    assert "·" in rendered
    assert "✗ crash_handler raised RuntimeError('handler exploded')" in rendered
    assert "\n  crash_handler()  " not in rendered
    assert "/doeff/do.py:52" not in rendered
    assert "\n\nRuntimeError: handler exploded" in rendered


def test_format_default_shows_program_yield_chain() -> None:
    def crash_handler(effect: object, _k: object):
        if isinstance(effect, Boom):
            raise RuntimeError("handler exploded")
        yield Delegate()

    @do
    def inner() -> Program[int]:
        yield Put("k", 1)
        yield Boom()
        return 1

    @do
    def outer() -> Program[int]:
        yield Put("k", 0)
        return (yield inner())

    result = run(WithHandler(crash_handler, outer()), handlers=default_handlers(), store={"k": 0})
    assert result.is_err()

    rendered = _tb_from_run_result(result).format_default()
    source_file = str(Path(__file__).resolve())
    outer_line = _line_of(outer.original_generator, 'yield Put("k", 0)')
    inner_line = _line_of(inner.original_generator, "yield Boom()")
    assert "outer()" in rendered
    assert "yield Put(" in rendered
    assert "inner()" in rendered
    assert "yield Boom" in rendered
    assert "crash_handler✗" in rendered
    assert "handler exploded" in rendered
    assert f"{source_file}:{outer_line}" in rendered
    assert f"{source_file}:{inner_line}" in rendered
    assert "\n  crash_handler()  " not in rendered
    assert "/doeff/do.py:52" not in rendered


def test_format_default_includes_resumed_effects() -> None:
    @do
    def body() -> Program[int]:
        yield Put("k", 1)
        raise ValueError("boom")
        yield

    result = run(body(), handlers=default_handlers(), store={"k": 0})
    assert result.is_err()

    rendered = _tb_from_run_result(result).format_default()
    assert "yield Put(" in rendered
    assert "→ resumed with" in rendered
    assert "raise ValueError('boom')" in rendered
    assert "/doeff/do.py:52" not in rendered


def test_format_default_shows_delegation_chain() -> None:
    def outer_crash_handler(effect: object, _k: object):
        if isinstance(effect, Boom):
            raise RuntimeError("delegated boom")
        yield Delegate()

    def inner_delegate_handler(_effect: object, _k: object):
        yield Delegate()

    @do
    def body() -> Program[int]:
        yield Boom()
        return 1

    result = run(
        WithHandler(outer_crash_handler, WithHandler(inner_delegate_handler, body())),
        handlers=default_handlers(),
    )
    assert result.is_err()

    rendered = _tb_from_run_result(result).format_default()
    assert "yield Boom" in rendered
    assert "inner_delegate_handler↗" in rendered
    assert "outer_crash_handler✗" in rendered
    assert "StateHandler·" in rendered
    assert "delegated boom" in rendered
    assert "\n  outer_crash_handler()  " not in rendered
    assert "\n\nRuntimeError: delegated boom" in rendered


def test_format_default_spawn_shows_effect_in_child() -> None:
    def crash_handler(effect: object, _k: object):
        if isinstance(effect, Boom):
            raise RuntimeError("child exploded")
        yield Delegate()

    @do
    def child() -> Program[int]:
        yield Boom()
        return 1

    @do
    def parent() -> Program[list[object]]:
        task = yield Spawn(WithHandler(crash_handler, child()))
        return (yield Gather(task))

    result = run(parent(), handlers=default_handlers())
    assert result.is_err()

    rendered = _tb_from_run_result(result).format_default()
    source_file = str(Path(__file__).resolve())
    gather_line = _line_of(parent.original_generator, "return (yield Gather(task))")
    spawn_line = _line_of(parent.original_generator, "task = yield Spawn(")
    assert "yield Boom" in rendered
    assert "crash_handler✗" in rendered
    assert "·" in rendered
    assert "child exploded" in rendered
    assert "── in task " in rendered
    assert "yield Gather(" in rendered
    assert "_program()" not in rendered
    assert "_spawn_task()" not in rendered
    assert "doeff/effects/gather.py" not in rendered
    assert f"parent()  {source_file}:{gather_line}" in rendered
    assert f"spawned at parent() {source_file}:{spawn_line}" in rendered
    boundary_pos = rendered.index("── in task ")
    gather_pos = rendered.index("yield Gather(")
    child_pos = rendered.index("  child()")
    assert gather_pos < boundary_pos < child_pos
    child_stack_line = next(
        line.strip() for line in rendered.splitlines() if line.strip().startswith("[crash_handler")
    )
    assert child_stack_line.count("ResultSafeHandler·") >= 2
    assert child_stack_line.count("WriterHandler·") >= 2
    assert child_stack_line.count("ReaderHandler·") >= 2
    assert child_stack_line.count("StateHandler·") >= 2
