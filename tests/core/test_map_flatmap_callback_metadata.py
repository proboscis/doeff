from __future__ import annotations

import warnings
from typing import Any

import pytest

from doeff import Program, default_handlers, do, run
from tests._run_helpers import run_with_defaults
# REMOVED: from doeff import ProgramCallStack







@pytest.mark.skip(reason="uses removed API: ProgramCallStack")
def test_callstack_includes_map_metadata() -> None:
    @do
    def source() -> Program[list[dict[str, Any]]]:
        return (yield ProgramCallStack())

    def mapper(stack: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return stack

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        result = run(Program.map(source(), mapper), handlers=default_handlers())

    assert result.is_ok(), result.error
    stack = result.value
    assert isinstance(stack, list)
    assert any(
        isinstance(frame, dict) and frame.get("function_name") == mapper.__name__
        for frame in stack
    )


@pytest.mark.skip(reason="uses removed API: ProgramCallStack")
def test_callstack_includes_flatmap_metadata() -> None:
    @do
    def source() -> Program[list[dict[str, Any]]]:
        return (yield ProgramCallStack())

    def binder(stack: list[dict[str, Any]]) -> Program[list[dict[str, Any]]]:
        return Program.pure(stack)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        result = run(Program.flat_map(source(), binder), handlers=default_handlers())

    assert result.is_ok(), result.error
    stack = result.value
    assert isinstance(stack, list)
    assert any(
        isinstance(frame, dict) and frame.get("function_name") == binder.__name__
        for frame in stack
    )
