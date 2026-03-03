from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path

from doeff import (
    Ask,
    Delegate,
    Effect,
    EffectBase,
    Pass,
    Program,
    Resume,
    Tell,
    WithHandler,
    WithIntercept,
    default_handlers,
    do,
    run,
)
from doeff.effects import Get, Put, StatePutEffect
from doeff.effects.gather import Gather
from doeff.effects.spawn import Spawn
from doeff.trace import ProgramYield
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


def _spawn_boundary_from(tb: object) -> object:
    for entry in getattr(tb, "active_chain", ()):
        if type(entry).__name__ == "SpawnBoundary":
            return entry
    raise AssertionError("expected spawn boundary in active_chain")


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
    assert "sync_await_handler ⇆" in rendered


def test_format_default_shows_async_await_handler_when_delegated() -> None:
    rendered = _render_single_delegated_handler("async_await_handler")
    assert "async_await_handler ⇆" in rendered


def test_format_default_shows_rust_await_handler_when_delegated() -> None:
    rendered = _render_single_delegated_handler("AwaitHandler")
    assert "AwaitHandler ⇆" in rendered


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
                    {"handler_name": "h_passed", "handler_kind": "python", "status": "passed"},
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
    assert "handlers:" in rendered
    assert "h_active ⚡" in rendered
    assert "· 1 pending" in rendered
    assert "h_passed ↗" in rendered
    assert "h_delegated ⇆" in rendered
    assert "h_resumed ✓" in rendered
    assert "h_transferred ⇢" in rendered
    assert "h_returned ✓" in rendered
    assert "h_threw ✗" in rendered


def test_format_default_distinguishes_passed_and_delegated_markers() -> None:
    tb = _tb(
        [
            {
                "kind": "effect_yield",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 20,
                "effect_repr": "Ping()",
                "handler_stack": [
                    {"handler_name": "h_passed", "handler_kind": "python", "status": "passed"},
                    {
                        "handler_name": "h_delegated",
                        "handler_kind": "python",
                        "status": "delegated",
                    },
                    {"handler_name": "h_resumed", "handler_kind": "python", "status": "resumed"},
                ],
                "result": {"kind": "resumed", "value_repr": "1"},
            },
            {
                "kind": "exception_site",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 21,
                "exception_type": "RuntimeError",
                "message": "boom",
            },
        ]
    )

    rendered = tb.format_default()
    assert "h_passed ↗" in rendered
    assert "h_delegated ⇆" in rendered
    assert "h_resumed ✓" in rendered


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


def test_format_default_handler_program_yield_python() -> None:
    tb = _tb(
        [
            {
                "kind": "program_yield",
                "function_name": "analyze_video_handler",
                "source_file": "video.py",
                "source_line": 164,
                "sub_program_repr": "_lower_analyze_video()",
                "handler_kind": "python",
            },
            {
                "kind": "exception_site",
                "function_name": "analyze_video_handler",
                "source_file": "video.py",
                "source_line": 165,
                "exception_type": "ValueError",
                "message": "boom",
            },
        ]
    )

    rendered = tb.format_default()
    assert "⚙ " not in rendered
    assert "analyze_video_handler()  video.py:164" not in rendered
    assert "yield _lower_analyze_video()" not in rendered


def test_format_default_handler_program_yield_rust_builtin() -> None:
    tb = _tb(
        [
            {
                "kind": "program_yield",
                "function_name": "StateHandler",
                "source_file": "<rust>",
                "source_line": 0,
                "sub_program_repr": "[MISSING] <sub_program>",
                "handler_kind": "rust_builtin",
            },
            {
                "kind": "exception_site",
                "function_name": "StateHandler",
                "source_file": "<rust>",
                "source_line": 0,
                "exception_type": "RuntimeError",
                "message": "boom",
            },
        ]
    )

    rendered = tb.format_default()
    assert "⚙ " not in rendered
    assert "(rust_builtin)" not in rendered
    assert "yield [MISSING] <sub_program>" not in rendered


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
    assert "h1 ⇆" in rendered
    assert "h2 ✓" in rendered
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
    assert rendered.count("h1 ⇆") == 1
    assert rendered.count("h2 ✓") == 1
    assert rendered.count("(same handlers)") == 1


def test_format_default_handler_stack_shows_source_locations() -> None:
    tb = _tb(
        [
            {
                "kind": "effect_yield",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 7,
                "effect_repr": "Ask('x')",
                "handler_stack": [
                    {
                        "handler_name": "py_handler",
                        "handler_kind": "python",
                        "source_file": "handlers/my_handler.py",
                        "source_line": 42,
                        "status": "resumed",
                    },
                    {
                        "handler_name": "StateHandler",
                        "handler_kind": "rust_builtin",
                        "source_file": None,
                        "source_line": None,
                        "status": "threw",
                    },
                ],
                "result": {
                    "kind": "threw",
                    "handler_name": "StateHandler",
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
    assert "handlers:" in rendered
    assert "py_handler ✓  handlers/my_handler.py:42" in rendered
    assert "StateHandler ✗  (rust_builtin)" in rendered


def test_format_default_handler_stack_collapses_pending_groups() -> None:
    tb = _tb(
        [
            {
                "kind": "effect_yield",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 9,
                "effect_repr": "Ping()",
                "handler_stack": [
                    {"handler_name": "pending_1", "handler_kind": "python", "status": "pending"},
                    {"handler_name": "pending_2", "handler_kind": "python", "status": "pending"},
                    {
                        "handler_name": "active_handler",
                        "handler_kind": "python",
                        "source_file": "handlers/active.py",
                        "source_line": 10,
                        "status": "resumed",
                    },
                    {"handler_name": "pending_3", "handler_kind": "python", "status": "pending"},
                    {"handler_name": "pending_4", "handler_kind": "python", "status": "pending"},
                    {"handler_name": "pending_5", "handler_kind": "python", "status": "pending"},
                ],
                "result": {"kind": "resumed", "value_repr": "1"},
            },
            {
                "kind": "exception_site",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 10,
                "exception_type": "RuntimeError",
                "message": "boom",
            },
        ]
    )

    rendered = tb.format_default()
    assert "· 2 pending" in rendered
    assert "active_handler ✓  handlers/active.py:10" in rendered
    assert "· 3 pending" in rendered
    assert "pending_1" not in rendered
    assert "pending_5" not in rendered


def test_format_default_handler_stack_all_pending_shows_no_match() -> None:
    tb = _tb(
        [
            {
                "kind": "effect_yield",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 11,
                "effect_repr": "Ping()",
                "handler_stack": [
                    {"handler_name": "p1", "handler_kind": "python", "status": "pending"},
                    {"handler_name": "p2", "handler_kind": "python", "status": "pending"},
                    {"handler_name": "p3", "handler_kind": "python", "status": "pending"},
                    {"handler_name": "p4", "handler_kind": "python", "status": "pending"},
                ],
                "result": {"kind": "active"},
            },
            {
                "kind": "exception_site",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 12,
                "exception_type": "RuntimeError",
                "message": "boom",
            },
        ]
    )

    rendered = tb.format_default()
    assert "· 4 pending (no handler matched)" in rendered


def test_format_default_handler_stack_mixed_pending_groups_keep_order() -> None:
    tb = _tb(
        [
            {
                "kind": "effect_yield",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 13,
                "effect_repr": "Ping()",
                "handler_stack": [
                    {"handler_name": "p1", "handler_kind": "python", "status": "pending"},
                    {
                        "handler_name": "delegate_handler",
                        "handler_kind": "python",
                        "source_file": "handlers/delegate.py",
                        "source_line": 7,
                        "status": "delegated",
                    },
                    {"handler_name": "p2", "handler_kind": "python", "status": "pending"},
                    {"handler_name": "p3", "handler_kind": "python", "status": "pending"},
                    {
                        "handler_name": "throw_handler",
                        "handler_kind": "python",
                        "source_file": "handlers/throw.py",
                        "source_line": 9,
                        "status": "threw",
                    },
                    {"handler_name": "p4", "handler_kind": "python", "status": "pending"},
                ],
                "result": {
                    "kind": "threw",
                    "handler_name": "throw_handler",
                    "exception_repr": "RuntimeError('boom')",
                },
            },
            {
                "kind": "exception_site",
                "function_name": "runner",
                "source_file": "program.py",
                "source_line": 14,
                "exception_type": "RuntimeError",
                "message": "boom",
            },
        ]
    )

    rendered = tb.format_default()
    first_pending = rendered.index("· 1 pending")
    delegated = rendered.index("delegate_handler ⇆  handlers/delegate.py:7")
    middle_pending = rendered.index("· 2 pending")
    threw = rendered.index("throw_handler ✗  handlers/throw.py:9")
    trailing_pending = rendered.rindex("· 1 pending")
    assert first_pending < delegated < middle_pending < threw < trailing_pending


def test_format_default_keeps_duplicate_handler_names_from_vm_chain() -> None:
    @do
    def same_name_handler(_effect: Effect, _k: object):
        yield Pass()

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
    assert rendered.count("same_name_handler ↗") >= 2


def test_format_default_duplicate_name_throw_marks_correct_handler() -> None:
    def _mk_handler(label: str):
        @do
        def handler(effect: Effect, k: object):
            if getattr(effect, "key", None) == "x":
                _ = yield Resume(k, f"{label}:resumed")
                raise RuntimeError(f"{label}:boom")
            yield Pass()

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
    assert "handler ✗" in rendered
    assert "pending" in rendered
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
    assert "sync_await_handler ⇆" in rendered
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


def test_format_default_spawn_separator_from_context_entry() -> None:
    tb = _tb(
        [
            {
                "kind": "effect_yield",
                "function_name": "parent",
                "source_file": "parent.py",
                "source_line": 22,
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
                    "target_repr": "child() child.py:3",
                },
            },
            {
                "kind": "program_yield",
                "function_name": "child",
                "source_file": "child.py",
                "source_line": 3,
                "sub_program_repr": "leaf()",
            },
            {
                "kind": "context_entry",
                "data": {
                    "kind": "spawn_boundary",
                    "task_id": 4,
                    "parent_task": 0,
                    "spawn_site": {
                        "function_name": "parent",
                        "source_file": "parent.py",
                        "source_line": 22,
                    },
                },
            },
            {
                "kind": "exception_site",
                "function_name": "child",
                "source_file": "child.py",
                "source_line": 4,
                "exception_type": "ValueError",
                "message": "boom",
            },
        ]
    )

    rendered = tb.format_default()
    gather_pos = rendered.index("yield Gather(task)")
    boundary_pos = rendered.index("── in task 4 (spawned at parent() parent.py:22) ──")
    child_pos = rendered.index("  child()  child.py:3")
    assert gather_pos < boundary_pos < child_pos


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


@dataclass(frozen=True, kw_only=True)
class ResumeProbe(EffectBase):
    value: int


def test_format_default_shows_effect_yield_on_handler_throw() -> None:
    @do
    def crash_handler(effect: Effect, _k: object):
        if isinstance(effect, Boom):
            raise RuntimeError("handler exploded")
        yield Pass()

    @do
    def body() -> Program[int]:
        yield Boom()
        return 1

    result = run(WithHandler(crash_handler, body()), handlers=default_handlers())
    assert result.is_err()

    rendered = _tb_from_run_result(result).format_default()
    assert "yield Boom" in rendered
    assert "crash_handler ✗" in rendered
    assert "·" in rendered
    assert "raised RuntimeError('handler exploded')" in rendered
    # With typed handler metadata, crash_handler now appears as a proper frame
    assert "crash_handler" in rendered
    assert "/doeff/do.py:52" not in rendered
    assert "\n\nRuntimeError: handler exploded" in rendered


def test_format_default_shows_program_yield_chain() -> None:
    @do
    def crash_handler(effect: Effect, _k: object):
        if isinstance(effect, Boom):
            raise RuntimeError("handler exploded")
        yield Pass()

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
    assert "outer()" in rendered
    assert "inner()" in rendered
    assert "yield Boom" in rendered
    assert "crash_handler ✗" in rendered
    assert "handler exploded" in rendered
    assert source_file in rendered
    assert "crash_handler" in rendered
    assert "/doeff/do.py:52" not in rendered


def test_runtime_marks_python_handler_program_frame() -> None:
    @do
    def crash_handler(effect: Effect, _k: object):
        if isinstance(effect, Boom):
            raise RuntimeError("handler exploded")
        yield Pass()

    @do
    def body() -> Program[int]:
        yield Boom()
        return 1

    result = run(WithHandler(crash_handler, body()), handlers=default_handlers())
    assert result.is_err()

    tb = _tb_from_run_result(result)
    handler_frames = [
        entry
        for entry in tb.active_chain
        if isinstance(entry, ProgramYield) and entry.function_name.endswith("crash_handler")
    ]
    assert handler_frames
    assert any(frame.is_handler and frame.handler_kind == "python" for frame in handler_frames)
    rendered = tb.format_default()
    assert "⚙ " not in rendered
    assert "(python)" not in rendered
    assert "crash_handler ✗" in rendered


def test_runtime_marks_rust_builtin_handler_program_frame() -> None:
    @do
    def crash_handler(effect: Effect, _k: object):
        if isinstance(effect, Boom):
            raise RuntimeError("handler exploded")
        yield Pass()

    @do
    def body() -> Program[int]:
        yield Put("k", 1)
        yield Boom()
        return 1

    result = run(WithHandler(crash_handler, body()), handlers=default_handlers(), store={"k": 0})
    assert result.is_err()

    tb = _tb_from_run_result(result)
    rust_handler_frames = [
        entry
        for entry in tb.active_chain
        if isinstance(entry, ProgramYield)
        and entry.is_handler
        and entry.handler_kind == "rust_builtin"
    ]
    assert rust_handler_frames
    rendered = tb.format_default()
    assert "⚙ " not in rendered
    assert "pending" in rendered


def test_runtime_handler_cleanup_after_resume_stays_tagged() -> None:
    @do
    def resume_then_cleanup(effect: Effect, k: object):
        if isinstance(effect, ResumeProbe):
            resumed_value = yield Resume(k, effect.value + 1)
            yield Put("after_resume", resumed_value)
            raise RuntimeError("cleanup boom")
        yield Pass()

    @do
    def body() -> Program[int]:
        return (yield ResumeProbe(value=41))

    result = run(
        WithHandler(resume_then_cleanup, body()),
        handlers=default_handlers(),
        store={"after_resume": 0},
    )
    assert result.is_err()

    tb = _tb_from_run_result(result)
    handler_frames = [
        entry
        for entry in tb.active_chain
        if isinstance(entry, ProgramYield) and entry.function_name.endswith("resume_then_cleanup")
    ]
    assert handler_frames
    assert all(frame.handler_kind == "python" for frame in handler_frames)
    rendered = tb.format_default()
    assert "⚙ " not in rendered
    assert "resume_then_cleanup" in rendered
    assert "cleanup boom" in rendered


def test_runtime_sub_program_called_from_handler_inherits_handler_kind() -> None:
    @dataclass(frozen=True, kw_only=True)
    class SomeEffect(EffectBase):
        pass

    @do
    def helper() -> Program[int]:
        raise RuntimeError("helper boom")
        yield

    @do
    def delegating_handler(effect: Effect, k: object):
        if isinstance(effect, SomeEffect):
            result = yield helper()
            return (yield Resume(k, result))
        yield Pass()

    @do
    def body() -> Program[int]:
        return (yield SomeEffect())

    result = run(WithHandler(delegating_handler, body()), handlers=default_handlers())
    assert result.is_err()

    tb = _tb_from_run_result(result)
    helper_frames = [
        entry
        for entry in tb.active_chain
        if isinstance(entry, ProgramYield) and entry.function_name.endswith("helper")
    ]
    assert helper_frames
    assert all(frame.handler_kind == "python" for frame in helper_frames)


def test_runtime_resumed_user_continuation_frame_is_not_handler() -> None:
    @do
    def resume_only_handler(effect: Effect, k: object):
        if isinstance(effect, ResumeProbe):
            return (yield Resume(k, effect.value + 1))
        yield Pass()

    @do
    def resumed_inner() -> Program[int]:
        raise ValueError("resumed body boom")
        yield

    @do
    def body() -> Program[int]:
        _ = yield ResumeProbe(value=1)
        return (yield resumed_inner())

    result = run(WithHandler(resume_only_handler, body()), handlers=default_handlers())
    assert result.is_err()

    tb = _tb_from_run_result(result)
    resumed_frames = [
        entry
        for entry in tb.active_chain
        if isinstance(entry, ProgramYield) and entry.function_name == "resumed_inner"
    ]
    assert resumed_frames
    assert all(frame.handler_kind is None for frame in resumed_frames)


def test_runtime_interceptor_frame_inside_handler_dispatch_is_not_handler() -> None:
    @do
    def put_crash_interceptor(effect: Effect):
        if isinstance(effect, StatePutEffect):
            _ = yield Get("intercepted")
            raise RuntimeError("interceptor boom")
        return effect

    @do
    def put_handler(effect: Effect, k: object):
        if isinstance(effect, ResumeProbe):
            yield Put("intercepted", 1)
            return (yield Resume(k, effect.value))
        yield Pass()

    @do
    def body() -> Program[int]:
        return (yield ResumeProbe(value=7))

    wrapped = WithIntercept(
        put_crash_interceptor,
        WithHandler(put_handler, body()),
        (StatePutEffect,),
        "include",
    )
    result = run(wrapped, handlers=default_handlers(), store={"intercepted": 0})
    assert result.is_err()

    tb = _tb_from_run_result(result)
    interceptor_frames = [
        entry
        for entry in tb.active_chain
        if isinstance(entry, ProgramYield) and entry.function_name.endswith("put_crash_interceptor")
    ]
    assert interceptor_frames
    assert all(frame.handler_kind is None for frame in interceptor_frames)


def test_runtime_nested_dispatch_preserves_handler_provenance() -> None:
    @dataclass(frozen=True, kw_only=True)
    class Outer(EffectBase):
        pass

    @dataclass(frozen=True, kw_only=True)
    class Inner(EffectBase):
        pass

    @do
    def nested_handler(effect: Effect, k: object):
        if isinstance(effect, Outer):
            _ = yield Inner()
            raise RuntimeError("nested boom")
        if isinstance(effect, Inner):
            return (yield Resume(k, 1))
        yield Pass()

    @do
    def body() -> Program[int]:
        return (yield Outer())

    result = run(WithHandler(nested_handler, body()), handlers=default_handlers())
    assert result.is_err()

    tb = _tb_from_run_result(result)
    handler_frames = [
        entry
        for entry in tb.active_chain
        if isinstance(entry, ProgramYield) and entry.function_name.endswith("nested_handler")
    ]
    assert handler_frames
    assert all(frame.handler_kind == "python" for frame in handler_frames)
    body_frames = [
        entry
        for entry in tb.active_chain
        if isinstance(entry, ProgramYield) and entry.function_name == "body"
    ]
    assert all(frame.handler_kind is None for frame in body_frames)


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
    @do
    def outer_crash_handler(effect: Effect, _k: object):
        if isinstance(effect, Boom):
            raise RuntimeError("delegated boom")
        yield Pass()

    @do
    def inner_delegate_handler(_effect: Effect, _k: object):
        yield Pass()

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
    assert "inner_delegate_handler ↗" in rendered
    assert "outer_crash_handler ✗" in rendered
    assert "pending" in rendered
    assert "delegated boom" in rendered
    assert "outer_crash_handler" in rendered
    assert "\n\nRuntimeError: delegated boom" in rendered


def test_format_default_runtime_distinguishes_passed_and_delegated() -> None:
    @dataclass(frozen=True, kw_only=True)
    class MarkerEffect(EffectBase):
        pass

    @do
    def outer_throw_handler(effect: Effect, _k: object):
        if isinstance(effect, MarkerEffect):
            raise RuntimeError("pass-vs-delegate boom")
        yield Pass()

    @do
    def middle_delegate_handler(effect: Effect, _k: object):
        if isinstance(effect, MarkerEffect):
            yield Delegate()
            return
        yield Pass()

    @do
    def inner_pass_handler(_effect: Effect, _k: object):
        yield Pass()

    @do
    def body() -> Program[int]:
        yield MarkerEffect()
        return 1

    result = run(
        WithHandler(
            outer_throw_handler,
            WithHandler(middle_delegate_handler, WithHandler(inner_pass_handler, body())),
        ),
        handlers=default_handlers(),
    )
    assert result.is_err()

    rendered = _tb_from_run_result(result).format_default()
    assert "inner_pass_handler ↗" in rendered
    assert "middle_delegate_handler ⇆" in rendered
    assert "outer_throw_handler ✗" in rendered
    assert "pass-vs-delegate boom" in rendered


def test_format_default_spawn_shows_effect_in_child() -> None:
    @do
    def crash_handler(effect: Effect, _k: object):
        if isinstance(effect, Boom):
            raise RuntimeError("child exploded")
        yield Pass()

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
    assert "yield Boom" in rendered
    assert "crash_handler ✗" in rendered
    assert "·" in rendered
    assert "child exploded" in rendered
    assert "── in task " in rendered
    assert "yield Gather(" in rendered
    assert "_program()" not in rendered
    assert "_spawn_task()" not in rendered
    assert "doeff/effects/gather.py" not in rendered
    assert "parent()" in rendered
    assert source_file in rendered
    assert "child()" in rendered
    assert "⚙ crash_handler()" not in rendered
    boundary_pos = rendered.index("── in task ")
    gather_pos = rendered.index("yield Gather(")
    child_pos = rendered.index("  child()")
    assert gather_pos < boundary_pos < child_pos
    assert "handlers:" in rendered
    assert "pending" in rendered


def test_spawn_site_attribution_under_single_delegate_handler() -> None:
    @do
    def crash_handler(effect: Effect, _k: object):
        if isinstance(effect, Boom):
            raise RuntimeError("child exploded")
        yield Pass()

    @do
    def child() -> Program[int]:
        yield Boom()
        return 1

    @do
    def parent_single() -> Program[list[object]]:
        task = yield Spawn(WithHandler(crash_handler, child()))
        return (yield Gather(task))

    result = run(parent_single(), handlers=default_handlers())
    assert result.is_err()

    tb = _tb_from_run_result(result)
    boundary = _spawn_boundary_from(tb)
    site = boundary.spawn_site
    assert site is not None

    source_file = str(Path(__file__).resolve())
    expected_line = _line_of(parent_single.func, "task = yield Spawn(")
    assert site.function_name == "parent_single"
    assert site.source_file == source_file
    assert site.source_line == expected_line
    assert f"spawned at parent_single() {source_file}:{expected_line}" in tb.format_default()


def test_spawn_site_attribution_under_nested_delegate_handlers() -> None:
    @do
    def crash_handler(effect: Effect, _k: object):
        if isinstance(effect, Boom):
            raise RuntimeError("child exploded")
        yield Pass()

    @do
    def child() -> Program[int]:
        yield Boom()
        return 1

    @do
    def parent_nested() -> Program[list[object]]:
        task = yield Spawn(WithHandler(crash_handler, child()))
        return (yield Gather(task))

    result = run(parent_nested(), handlers=default_handlers())
    assert result.is_err()

    tb = _tb_from_run_result(result)
    boundary = _spawn_boundary_from(tb)
    site = boundary.spawn_site
    assert site is not None

    source_file = str(Path(__file__).resolve())
    expected_line = _line_of(parent_nested.func, "task = yield Spawn(")
    assert site.function_name == "parent_nested"
    assert site.source_file == source_file
    assert site.source_line == expected_line
    rendered = tb.format_default()
    assert f"spawned at parent_nested() {source_file}:{expected_line}" in rendered
    assert "spawned at spawn_intercept_handler()" not in rendered


def test_format_default_handler_tell_not_in_traceback() -> None:
    """Stale Tell dispatch in handler frame must not leak into traceback.

    Handler yields Tell() then Resume(k).  Tell completes instantly but
    frame_dispatch still maps handler → Tell dispatch.  Handler frame stays
    on stack (suspended at yield Resume) so the traceback incorrectly shows
    ``yield Tell(...)`` as an EffectYield.
    """

    @do
    def logging_handler(effect: Effect, k: object):
        if isinstance(effect, StatePutEffect):
            yield Tell("handler-internal-log")
            return (yield Resume(k, None))
        yield Pass()

    @do
    def body() -> Program[None]:
        yield Put("key", 1)
        raise ValueError("boom")

    result = run(
        WithHandler(logging_handler, body()),
        handlers=default_handlers(),
        store={"key": 0},
    )
    assert result.is_err()

    tb = _tb_from_run_result(result)
    rendered = tb.format_default()

    assert "Tell(" not in rendered, (
        f"Handler's stale Tell dispatch leaked into traceback:\n{rendered}"
    )
    assert "handler-internal-log" not in rendered
    assert "ValueError" in rendered
    assert "boom" in rendered


def test_format_default_handler_tell_not_in_traceback_deep() -> None:
    """Same stale-Tell bug but program continues yielding effects after
    handler resumes, keeping handler frame on stack longer.
    """

    @do
    def logging_handler(effect: Effect, k: object):
        if isinstance(effect, StatePutEffect):
            yield Tell("handler-internal-log")
            return (yield Resume(k, None))
        yield Pass()

    @do
    def inner() -> Program[None]:
        _ = yield Get("key")
        raise ValueError("deep-boom")

    @do
    def body() -> Program[None]:
        yield Put("key", 1)
        yield inner()

    result = run(
        WithHandler(logging_handler, body()),
        handlers=default_handlers(),
        store={"key": 0},
    )
    assert result.is_err()

    tb = _tb_from_run_result(result)
    rendered = tb.format_default()

    assert "Tell(" not in rendered, (
        f"Handler's stale Tell dispatch leaked into traceback:\n{rendered}"
    )
    assert "handler-internal-log" not in rendered
    assert "deep-boom" in rendered
