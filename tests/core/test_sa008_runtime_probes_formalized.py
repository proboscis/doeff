"""Formalized regressions converted from ad-hoc `uv run python` probes.

This file exists to ensure manual runtime investigations become repeatable tests.
"""

from __future__ import annotations

from doeff import Get, do
from tests._run_helpers import run_with_defaults


def test_probe_run_simple_program_returns_scalar() -> None:
    @do
    def program():
        return 1

    result = run_with_defaults(program())
    assert result.value == 1


def test_probe_run_bare_get_returns_value_from_store() -> None:
    result = run_with_defaults(Get("x"), store={"x": 99})
    assert result.value == 99


def test_probe_run_two_gets_returns_tuple() -> None:
    @do
    def program():
        left = yield Get("x")
        right = yield Get("y")
        return (left, right)

    result = run_with_defaults(program(), store={"x": 9, "y": 8})
    assert result.value == (9, 8)
