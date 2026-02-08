"""Formalized regressions converted from ad-hoc `uv run python` probes.

This file exists to ensure manual runtime investigations become repeatable tests.
"""

from __future__ import annotations

import doeff_vm

from doeff import Ask, Get, WithHandler, do
from doeff.rust_vm import default_handlers, run


def test_probe_withhandler_accepts_rust_handler_sentinel() -> None:
    sentinel = default_handlers()[0]
    expr = Ask("key")
    control = WithHandler(sentinel, expr)
    assert type(control).__name__ == "WithHandler"


def test_probe_kpc_is_runtime_effectbase_instance() -> None:
    @do
    def program():
        return 1

    kpc = program()
    assert isinstance(kpc, doeff_vm.EffectBase)
    assert isinstance(kpc, doeff_vm.KleisliProgramCall)


def test_probe_run_simple_program_returns_scalar() -> None:
    @do
    def program():
        return 1

    result = run(program(), handlers=default_handlers())
    assert result.value == 1


def test_probe_run_bare_get_returns_value_from_store() -> None:
    result = run(Get("x"), handlers=default_handlers(), store={"x": 99})
    assert result.value == 99


def test_probe_run_two_gets_returns_tuple() -> None:
    @do
    def program():
        left = yield Get("x")
        right = yield Get("y")
        return (left, right)

    result = run(program(), handlers=default_handlers(), store={"x": 9, "y": 8})
    assert result.value == (9, 8)
