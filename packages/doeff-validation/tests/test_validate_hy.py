"""doeff-validation の Hy のマクロの検査 — defk の中の validate / check と、展開の時点の誤り。"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import hy
import pytest
from doeff_core_effects.handlers import reader
from doeff_core_effects.scheduler import scheduled
from doeff_traverse import parallel, sequential
from doeff_validation import CheckError, CheckFailure, ValidationException

from doeff import run

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validation_cases as cases

ENV = {"seats-a": 3, "seats-n1": 5, "seats-empty": 0}


def _run(program: object, traverse_handler: Callable[[object], object] | None = None) -> object:
    """検査の Program を、reader と traverse の handler(既定は逐次)の下で走らせる。"""
    handler = traverse_handler if traverse_handler is not None else sequential()
    return run(scheduled(reader(ENV)(handler(program))))


def _failures(program: object, traverse_handler: Callable[[object], object] | None = None) -> ValidationException:
    """ValidationException が投げられることを確かめ、その例外を返す。"""
    with pytest.raises(ValidationException) as caught:
        _run(program, traverse_handler)
    return caught.value


def _expand(source: str) -> None:
    """Hy の断片を読み込んで評価する(展開の時点の誤りを確かめる)。"""
    hy.eval(
        hy.read_many(
            "(require doeff-hy.macros [defk <- validate check])\n" + source
        ),
        {"__name__": "validation_snippet"},
    )


def test_bang_validate_returns_none_when_all_pass() -> None:
    assert _run(cases.all_independent("k", "k", "pending")) is None


def test_flat_and_parenthesized_checks_record_expression_and_values() -> None:
    error = _failures(cases.all_independent("k-1", "k-2", "running"))
    assert error.reasons == [cases.Reason.KEY, cases.Reason.PHASE]
    key, phase = error.failures
    assert isinstance(key, CheckFailure)
    assert key.expression == "(= key expected-key)"
    assert [(a.source, a.value) for a in key.arguments] == [("key", "k-1"), ("expected-key", "k-2")]
    assert phase.expression == '(= phase "pending")'
    assert [(a.source, a.value) for a in phase.arguments] == [("phase", "running"), ('"pending"', "pending")]


@pytest.mark.parametrize("handler_factory", [sequential, lambda: parallel(4)])
def test_effect_and_defk_call_arguments_run_inside_their_items(handler_factory) -> None:
    error = _failures(cases.effectful_arguments("n1"), handler_factory())
    effect_arg, defk_arg = error.failures
    assert effect_arg.arguments[0].source == '(! (Ask "seats-a"))'
    assert effect_arg.arguments[0].value == 3
    assert defk_arg.arguments[0].source == "(! (count-seats node))"
    assert defk_arg.arguments[0].value == 5


def test_failing_bang_argument_is_collected_and_other_items_still_run() -> None:
    error = _failures(cases.failing_evaluation())
    evaluation, plain = error.failures
    assert isinstance(evaluation, CheckError)
    assert isinstance(evaluation.error, KeyError)
    assert evaluation.expression == '(= (! (Ask "missing")) 0)'
    assert isinstance(plain, CheckFailure)


def test_unmarked_program_argument_is_compared_as_a_value() -> None:
    error = _failures(cases.unmarked_program_argument())
    assert len(error.failures) == 1
    assert error.failures[0].reason is cases.Reason.SEATS


def test_nested_validate_is_one_failure() -> None:
    error = _failures(cases.nested("running"))
    nested, own = error.failures
    assert isinstance(nested, ValidationException)
    assert nested.reasons == [cases.Reason.PHASE, cases.Reason.KEY]
    assert own.reason is cases.Reason.SEATS


def test_parenthesized_and_is_not_decomposed() -> None:
    error = _failures(cases.short_circuit(None))
    failure = error.failures[0]
    assert failure.expression == "(and (is-not x None) (> x.size 0))"
    assert failure.arguments[0].value is False


def test_check_in_defk_body_is_an_expansion_error() -> None:
    with pytest.raises(hy.errors.HyMacroExpansionError, match="validate の直下と"):
        _expand(
            "(defk helper [x] {:pre [(: x int)] :post [(: % NoneType)]}\n"
            "  (check = x 0))"
        )


def test_check_at_top_level_is_an_expansion_error() -> None:
    with pytest.raises(hy.errors.HyMacroExpansionError, match="validate の直下と"):
        _expand("(check = 1 0)")


def test_bare_bind_directly_under_validate_is_an_expansion_error() -> None:
    with pytest.raises(hy.errors.HyMacroExpansionError, match=r"直下に \(<- …\) は書けません"):
        _expand("(validate (<- x (f)) (check = x 0))")


def test_flat_short_circuit_operator_is_an_expansion_error() -> None:
    with pytest.raises(hy.errors.HyMacroExpansionError, match="括弧の形で書いてください"):
        _expand("(validate (check and a b))")


def test_unknown_keyword_is_an_expansion_error() -> None:
    with pytest.raises(hy.errors.HyMacroExpansionError, match="知らない keyword"):
        _expand("(validate (check = a b :because R))")


def test_pre_checks_collect_every_failure_with_function_and_phase() -> None:
    error = _failures(cases.bind_seat("k-1", "k-2", "running", "empty"))
    assert error.context == "bind-seat pre-condition"
    assert error.reasons == [cases.Reason.KEY, cases.Reason.PHASE, cases.Reason.SEATS]
    seats = error.failures[2]
    assert seats.arguments[0].source == "(! (count-seats node))"
    assert seats.arguments[0].value == 0
    assert "bind-seat pre-condition: 3 件の検査が落ちました" in str(error)


def test_pre_checks_pass_and_body_runs() -> None:
    assert _run(cases.bind_seat("k", "k", "pending", "n1")) == "k@n1"


def test_post_check_sees_the_return_value() -> None:
    error = _failures(cases.empty_result("x"))
    assert error.context == "empty-result post-condition"
    assert error.failures[0].arguments[0].source == "%"


def test_type_contract_still_fails_first_with_assertion_error() -> None:
    with pytest.raises(AssertionError, match="pre-condition type error"):
        _run(cases.bind_seat(1, "k", "pending", "n1"))


def test_legacy_boolean_contract_is_unchanged() -> None:
    with pytest.raises(AssertionError, match="pre-condition failed"):
        _run(cases.legacy_contract(0))


def test_check_in_deff_contract_is_an_expansion_error() -> None:
    with pytest.raises(hy.errors.HyMacroExpansionError, match="defk / do! の契約にだけ"):
        hy.eval(
            hy.read_many(
                "(require doeff-hy.macros [deff])\n"
                "(deff pure [x] {:pre [(: x int) (check > x 0)] :post [(: % int)]} x)"
            ),
            {"__name__": "validation_snippet"},
        )
