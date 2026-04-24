from __future__ import annotations

import inspect

import pytest

from doeff import Ask, Effect, Pass, Program, Resume, WithHandler, default_handlers, do, run
from doeff import Put
from doeff_core_effects.scheduler import Gather
from doeff_core_effects.scheduler import Spawn
from tests._run_helpers import run_with_defaults
# REMOVED: from doeff.traceback import attach_doeff_traceback

_DEFAULT_HANDLER_NAMES = (
    "sync_await_handler",
    "spawn_intercept_handler",
    "LazyAskHandler",
    "SchedulerHandler",
    "ResultSafeHandler",
    "WriterHandler",
    "ReaderHandler",
    "StateHandler",
)


def _line_of(function: object, needle: str) -> int:
    lines, start = inspect.getsourcelines(function)
    for offset, line in enumerate(lines):
        if needle in line:
            return start + offset
    raise AssertionError(f"failed to find {needle!r} in source")


def _handler_lines_after_effect(
    lines: list[str],
    *,
    effect_fragment: str,
    detail_fragment: str | None = None,
) -> list[str]:
    for idx, raw in enumerate(lines):
        text = raw.strip()
        if not text.startswith("yield "):
            continue
        if effect_fragment not in text:
            continue
        if detail_fragment is not None and detail_fragment not in text:
            continue

        for follow_idx in range(idx + 1, len(lines)):
            candidate = lines[follow_idx].strip()
            if candidate == "handlers:":
                block: list[str] = []
                for entry in lines[follow_idx + 1 :]:
                    entry_text = entry.strip()
                    if entry_text == "":
                        break
                    if entry_text.startswith(("→ ", "✗ ", "⇢ ", "yield ", "raise ", "handlers:")):
                        break
                    if entry_text == "(same handlers)":
                        break
                    block.append(entry_text)
                return block
            if candidate == "(same handlers)":
                return [candidate]
            if candidate.startswith(("yield ", "raise ", "→ ", "✗ ", "⇢ ")):
                break
    raise AssertionError(f"handler lines not found after effect {effect_fragment!r}")


def _first_line_index(lines: list[str], fragment: str) -> int:
    for idx, line in enumerate(lines):
        if fragment in line:
            return idx
    raise AssertionError(f"line containing {fragment!r} not found")


def _assert_default_handlers_visible(handler_lines: list[str]) -> None:
    assert handler_lines
    assert all("..." not in line for line in handler_lines)
    assert any(name in line for line in handler_lines for name in _DEFAULT_HANDLER_NAMES) or any(
        "pending" in line for line in handler_lines
    )


def _assert_basic_structure(rendered: str, *, exception_type: str) -> list[str]:
    lines = rendered.strip().splitlines()
    assert lines[0] == "doeff Traceback (most recent call last):"
    assert any("()" in line for line in lines)
    assert any("yield " in line for line in lines) or any("raise " in line for line in lines)

    handler_markers = [
        line.strip() for line in lines if line.strip() in {"handlers:", "(same handlers)"}
    ]
    if handler_markers:
        assert any("✗ " in line or "⇢ " in line for line in lines)
    assert lines[-1].startswith(exception_type)
    return lines


def _render_failure(
    program: object,
    *,
    env: dict[object, object] | None = None,
    store: dict[str, object] | None = None,
) -> str:
    result = run_with_defaults(program, env=env, store=store)
    assert result.is_err()
    doeff_tb = attach_doeff_traceback(result.error, traceback_data=result.traceback_data)
    assert doeff_tb is not None
    return doeff_tb.format_default()








