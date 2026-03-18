from __future__ import annotations

import pytest

import doeff as doeff_module
from doeff import (
    AllocVar,
    Apply,
    Ask,
    Discontinue,
    Eval,
    EvalInScope,
    Expand,
    Perform,
    Pure,
    ReadVar,
    ResumeContinuation,
    WriteVar,
    WriteVarNonlocal,
    default_handlers,
    do,
    run,
)


def test_doctrl_exports_are_available() -> None:
    assert Pure is not None
    assert Apply is not None
    assert Expand is not None
    assert Eval is not None
    assert EvalInScope is not None
    assert AllocVar is not None
    assert ReadVar is not None
    assert WriteVar is not None
    assert WriteVarNonlocal is not None
    assert Perform is not None
    assert Discontinue is not None
    assert ResumeContinuation is not None
    with pytest.raises(AttributeError):
        doeff_module.Finally


def _meta() -> dict[str, object]:
    return {
        "function_name": "test_fn",
        "source_file": __file__,
        "source_line": 1,
    }


def test_pure_apply_eval_execute() -> None:
    pure_result = run(Pure(123))
    assert pure_result.value == 123

    def add(a: int, b: int) -> int:
        return a + b

    apply_result = run(Apply(Pure(add), [Pure(1), Pure(2)], {}, _meta()))
    assert apply_result.value == 3

    @do
    def identity(x: int):
        return x

    expand_result = run(identity(4))
    assert expand_result.value == 4

    eval_result = run(Eval(Perform(Ask("k"))), handlers=default_handlers(), env={"k": "value"})
    assert eval_result.value == "value"

    perform_result = run(Perform(Ask("k")), env={"k": "perform-value"}, handlers=default_handlers())
    assert perform_result.value == "perform-value"


def test_apply_delivers_doexpr_result_as_raw_value() -> None:
    returned = Pure(123)

    def return_program() -> Pure:
        return returned

    result = run(Apply(Pure(return_program), [], {}, _meta()))
    assert result.value is returned


def test_apply_delivers_effect_result_as_raw_value() -> None:
    returned = Ask("k")

    def return_effect():
        return returned

    result = run(
        Apply(Pure(return_effect), [], {}, _meta()),
        handlers=default_handlers(),
        env={"k": "value"},
    )
    assert result.value is returned


def test_expand_evaluates_doexpr_result() -> None:
    def return_program() -> Pure:
        return Pure(456)

    result = run(Expand(Pure(return_program), [], {}, _meta()))
    assert result.value == 456


def test_expand_rejects_plain_value_result() -> None:
    def return_value() -> int:
        return 456

    result = run(Expand(Pure(return_value), [], {}, _meta()))
    assert result.is_err()
    assert isinstance(result.error, TypeError)
    assert "ExpandReturn: expected DoeffGenerator, DoExpr, or EffectBase" in str(result.error)


def test_apply_requires_meta() -> None:
    def add(a: int, b: int) -> int:
        return a + b

    with pytest.raises(TypeError, match=r"Apply\.meta is required"):
        Apply(Pure(add), [1, 2], {})


def test_expand_requires_meta() -> None:
    def make_program(x: int) -> int:
        return x

    with pytest.raises(TypeError, match=r"Expand\.meta is required"):
        Expand(Pure(make_program), [Pure(1)], {})


def test_resume_continuation_requires_k() -> None:
    with pytest.raises(TypeError, match=r"K"):
        ResumeContinuation("not_k", Ask("x"))
