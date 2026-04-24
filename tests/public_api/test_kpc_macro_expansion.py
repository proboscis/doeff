from __future__ import annotations

from collections.abc import Generator
from typing import Any

from doeff import Ask, Effect, Program, do
from doeff_vm import Expand, Perform, Pure


@do
def takes_plain(value: Any) -> Generator[Program[Any], Any, Any]:
    if False:  # pragma: no cover
        yield Program.pure(None)
    return value


@do
def takes_program(p: Program[int]) -> Generator[Program[Any], Any, int]:
    value = yield p
    return value


@do
def takes_effect(e: Effect) -> Generator[Program[Any], Any, str]:
    if False:  # pragma: no cover
        yield Program.pure(None)
    return f"{type(e).__name__}:{getattr(e, 'key', '<missing>')}"


@do
def inner_program() -> Generator[Program[Any], Any, int]:
    if False:  # pragma: no cover
        yield Program.pure(None)
    return 7


def test_do_call_returns_expand_doctrl() -> None:
    result = takes_plain(1)
    assert isinstance(result, Expand), f"expected Expand DoCtrl, got {type(result).__name__}"
