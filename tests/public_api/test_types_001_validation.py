"""Public API validation tests aligned to strict SA-008 policy.

Policy:
- runtime DoExpr boundary is strict (Rust runtime bases)
- no duck-typed generator/program coercion
- clear TypeError on invalid boundary values
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from doeff import (
    Ask,
    Delegate,
    Get,
    K,
    Pass,
    Perform,
    Resume,
    Transfer,
    WithHandler,
    default_handlers,
    run,
)


def test_run_rejects_generator_function_input() -> None:
    def gen():
        yield Get("x")

    with pytest.raises(TypeError, match=r"DoExpr"):
        run(gen)


def test_run_rejects_raw_generator_input() -> None:
    def gen():
        yield Get("x")

    with pytest.raises(TypeError, match=r"DoExpr"):
        run(gen())


def test_run_accepts_bare_rust_effectbase() -> None:
    result = run(Get("x"), handlers=default_handlers(), store={"x": 99})
    assert result.value == 99


def test_run_rejects_non_dict_env_for_valid_program() -> None:
    with pytest.raises(TypeError, match=r"dict"):
        run(Get("x"), handlers=default_handlers(), env=cast(Any, "not_a_dict"))


def test_run_rejects_non_dict_store_for_valid_program() -> None:
    with pytest.raises(TypeError, match=r"dict"):
        run(Get("x"), handlers=default_handlers(), store=cast(Any, [1, 2, 3]))


def test_withhandler_expr_requires_rust_runtime_bases() -> None:
    def handler(_effect, _k):
        yield Delegate()

    with pytest.raises(TypeError, match=r"DoExpr"):
        WithHandler(handler, object())


def test_withhandler_accepts_rust_effect_expr() -> None:
    def handler(_effect, _k):
        yield Delegate()

    ctrl = WithHandler(handler, Perform(Ask("key")))
    assert type(ctrl).__name__ == "WithHandler"


def test_resume_transfer_require_real_k() -> None:
    with pytest.raises(TypeError, match=r"K"):
        Resume("not_k", Ask("x"))
    with pytest.raises(TypeError, match=r"K"):
        Transfer("not_k", Ask("x"))


def test_python_handler_receives_k_for_resume() -> None:
    seen: dict[str, bool] = {"is_k": False}

    def handler(_effect, k):
        seen["is_k"] = isinstance(k, K)
        return (yield Resume(k, "override"))

    result = run(
        WithHandler(handler, Perform(Ask("x"))),
        handlers=default_handlers(),
        env={"x": "original"},
    )

    assert seen["is_k"] is True
    assert result.value == "override"


def test_python_handler_transfer_and_delegate_with_k() -> None:
    transfer_seen: dict[str, bool] = {"is_k": False}

    def transfer_handler(_effect, k):
        transfer_seen["is_k"] = isinstance(k, K)
        yield Transfer(k, "via-transfer")

    transfer_result = run(
        WithHandler(transfer_handler, Perform(Ask("x"))),
        handlers=default_handlers(),
        env={"x": "original"},
    )

    delegate_seen: dict[str, bool] = {"is_k": False}

    def delegate_handler(_effect, k):
        delegate_seen["is_k"] = isinstance(k, K)
        yield Pass()

    delegate_result = run(
        WithHandler(delegate_handler, Perform(Ask("x"))),
        handlers=default_handlers(),
        env={"x": "original"},
    )

    assert transfer_seen["is_k"] is True
    assert transfer_result.value == "via-transfer"
    assert delegate_seen["is_k"] is True
    assert delegate_result.value == "original"


def test_vm_doexpr_hierarchy_is_exposed() -> None:
    import doeff_vm

    assert hasattr(doeff_vm, "DoExpr")
    assert not issubclass(doeff_vm.EffectBase, doeff_vm.DoExpr)
    assert issubclass(doeff_vm.DoCtrlBase, doeff_vm.DoExpr)
