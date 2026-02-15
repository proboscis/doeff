from __future__ import annotations

import pytest

from doeff import Ask, Call, Eval, Perform, Pure, ResumeContinuation, default_handlers, run


def test_doctrl_exports_are_available() -> None:
    assert Pure is not None
    assert Call is not None
    assert Eval is not None
    assert Perform is not None
    assert ResumeContinuation is not None


def test_pure_call_eval_execute() -> None:
    pure_result = run(Pure(123))
    assert pure_result.value == 123

    def add(a: int, b: int) -> int:
        return a + b

    call_result = run(Call(Pure(add), [Pure(1), Pure(2)], {}))
    assert call_result.value == 3

    eval_result = run(Eval(Perform(Ask("k")), default_handlers()), env={"k": "value"})
    assert eval_result.value == "value"

    perform_result = run(Perform(Ask("k")), env={"k": "perform-value"}, handlers=default_handlers())
    assert perform_result.value == "perform-value"


def test_call_requires_doexpr_for_f() -> None:
    def add(a: int, b: int) -> int:
        return a + b

    with pytest.raises(TypeError, match=r"Call\.f must be DoExpr"):
        Call(add, [Pure(1), Pure(2)], {})


def test_call_requires_doexpr_for_args_and_kwargs() -> None:
    def add(a: int, b: int) -> int:
        return a + b

    with pytest.raises(TypeError, match=r"Call\.args values must be DoExpr"):
        Call(Pure(add), [1, Pure(2)], {})

    with pytest.raises(TypeError, match=r"Call\.kwargs values must be DoExpr"):
        Call(Pure(add), [Pure(1)], {"b": 2})


def test_resume_continuation_requires_k() -> None:
    with pytest.raises(TypeError, match=r"K"):
        ResumeContinuation("not_k", Ask("x"))
