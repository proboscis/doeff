"""Public API validation tests aligned to strict SA-008 policy.

Policy:
- runtime DoExpr boundary is strict (Rust runtime bases)
- no duck-typed generator/program coercion
- clear TypeError on invalid boundary values
"""

from __future__ import annotations

from typing import Any, cast

import doeff
import pytest

from doeff import (
    Ask,
    Effect,
    Get,
    K,
    Pass,
    Perform,
    Resume,
    Transfer,
    WithHandler,
    do,
    run,
)
from tests._run_helpers import run_with_defaults


def test_run_rejects_generator_function_input() -> None:
    def gen():
        yield Get("x")

    with pytest.raises(TypeError, match=r"DoExpr"):
        run(gen)



def test_run_accepts_bare_rust_effectbase() -> None:
    result = run_with_defaults(Get("x"), store={"x": 99})
    assert result.value == 99





def test_withhandler_accepts_rust_effect_expr() -> None:
    @do
    def handler(_effect: Effect, _k):
        yield _effect

    ctrl = WithHandler(handler, Perform(Ask("key")))
    assert type(ctrl).__name__ == "WithHandler"


def test_withhandler_rejects_return_clause_keyword() -> None:
    @do
    def handler(_effect: Effect, _k):
        yield _effect

    @do
    def body():
        return "ok"
        yield

    with_handler = cast(Any, WithHandler)
    kwargs = {"return_clause": lambda value: value}

    with pytest.raises(TypeError, match=r"return_clause|unexpected keyword"):
        with_handler(handler, body(), **kwargs)


def test_doeff_vm_withhandler_rejects_return_clause_keyword() -> None:
    import doeff_vm

    @do
    def handler(_effect: Effect, _k):
        yield _effect

    @do
    def body():
        return "ok"
        yield

    with_handler = cast(Any, doeff_vm.WithHandler)
    kwargs = {"return_clause": lambda value: value}

    with pytest.raises(TypeError, match=r"return_clause|unexpected keyword"):
        with_handler(handler, body(), **kwargs)


def test_withhandler_rejects_third_positional_argument() -> None:
    @do
    def handler(_effect: Effect, _k):
        yield _effect

    args = (handler, Perform(Ask("key")), lambda value: value)
    with_handler = cast(Any, WithHandler)

    with pytest.raises(TypeError, match=r"positional arguments|given"):
        with_handler(*args)



def test_python_handler_receives_k_for_resume() -> None:
    seen: dict[str, bool] = {"is_k": False}

    @do
    def handler(_effect: Effect, k):
        seen["is_k"] = isinstance(k, K)
        return (yield Resume(k, "override"))

    result = run_with_defaults(
        WithHandler(handler, Perform(Ask("x"))),
        env={"x": "original"},
    )

    assert seen["is_k"] is True
    assert result.value == "override"


def test_python_handler_transfer_and_delegate_with_k() -> None:
    transfer_seen: dict[str, bool] = {"is_k": False}

    @do
    def transfer_handler(_effect: Effect, k):
        transfer_seen["is_k"] = isinstance(k, K)
        yield Transfer(k, "via-transfer")

    transfer_result = run_with_defaults(
        WithHandler(transfer_handler, Perform(Ask("x"))),
        env={"x": "original"},
    )

    delegate_seen: dict[str, bool] = {"is_k": False}

    @do
    def delegate_handler(_effect: Effect, k):
        delegate_seen["is_k"] = isinstance(k, K)
        yield Pass(_effect, k)

    delegate_result = run_with_defaults(
        WithHandler(delegate_handler, Perform(Ask("x"))),
        env={"x": "original"},
    )

    assert transfer_seen["is_k"] is True
    assert transfer_result.value == "via-transfer"
    assert delegate_seen["is_k"] is True
    assert delegate_result.value == "original"
