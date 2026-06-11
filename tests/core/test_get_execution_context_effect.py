from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from doeff_vm import (
    GetExecutionContext,
    Pass,
    Resume,
    WithHandler,
)

# REMOVED: from doeff import ProgramCallStack
from doeff import (
    Delegate,
    Effect,
    Program,
    do,
)
from tests._run_helpers import run_with_defaults

# REMOVED: from doeff.traceback import build_doeff_traceback

ROOT = Path(__file__).resolve().parents[2]


def _line_of(function: object, needle: str) -> int:
    lines, start = inspect.getsourcelines(function)
    for offset, line in enumerate(lines):
        if needle in line:
            return start + offset
    raise AssertionError(f"failed to find {needle!r} in source")


def _entries_from_error(error: BaseException) -> list[Any]:
    context = getattr(error, "doeff_execution_context", None)
    if context is None:
        return []
    entries = getattr(context, "entries", None)
    if entries is None:
        return []
    return list(entries)






def test_base_exception_bypasses_get_execution_context_conversion() -> None:
    seen: list[str] = []

    @do
    def observer(effect: Effect, k: object):
        if isinstance(effect, GetExecutionContext):
            seen.append("called")
            context = yield Delegate()
            return (yield Resume(k, context))
        yield Pass()

    @do
    def failing_program() -> Program[None]:
        raise KeyboardInterrupt("stop")

    wrapped = WithHandler(observer, failing_program())
    result = run_with_defaults(wrapped)
    assert result.is_err()
    assert isinstance(result.error, KeyboardInterrupt)
    assert seen == []








def _active_chain_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [entry for entry in value if isinstance(entry, dict)]






def test_get_execution_context_active_chain_no_exception_site() -> None:
    @do
    def program() -> Program[object]:
        return (yield GetExecutionContext())

    result = run_with_defaults(program())
    assert result.is_ok(), result.error
    entries = _active_chain_entries(getattr(result.value, "active_chain", None))
    assert not any(entry.get("kind") == "exception_site" for entry in entries)
