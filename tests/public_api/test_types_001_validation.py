"""Public API validation tests aligned to strict SA-008 policy.

Policy:
- runtime DoExpr boundary is strict (Rust runtime bases)
- no duck-typed generator/program coercion
- clear TypeError on invalid boundary values
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from doeff import Ask, Delegate, Get, Resume, Transfer, WithHandler
from doeff.do import do
from doeff.rust_vm import default_handlers, run


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

    with pytest.raises(TypeError, match=r"Rust DoExpr base"):
        WithHandler(handler, object())


def test_withhandler_accepts_rust_effect_expr() -> None:
    def handler(_effect, _k):
        yield Delegate()

    ctrl = WithHandler(handler, Ask("key"))
    assert type(ctrl).__name__ == "WithHandler"


def test_resume_transfer_require_real_k() -> None:
    with pytest.raises(TypeError, match=r"K"):
        Resume("not_k", Ask("x"))
    with pytest.raises(TypeError, match=r"K"):
        Transfer("not_k", Ask("x"))
