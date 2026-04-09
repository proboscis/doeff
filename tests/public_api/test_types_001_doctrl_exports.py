from __future__ import annotations

import pytest

import pytest

from doeff import (
    Apply,
    Ask,
    Expand,
    Pure,
    default_handlers,
    run,
)


def _meta() -> dict[str, object]:
    return {
        "function_name": "test_fn",
        "source_file": __file__,
        "source_line": 1,
    }


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


