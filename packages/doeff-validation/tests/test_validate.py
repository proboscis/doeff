"""doeff-validation の Python の API の検査 — 集める・並行・fail-fast・入れ子・効果の引数。"""

from __future__ import annotations

import operator
from collections.abc import Callable
from enum import Enum

import pytest
from doeff_core_effects import Ask
from doeff_core_effects.handlers import reader
from doeff_core_effects.scheduler import scheduled
from doeff_traverse import parallel, parallel_fail_fast, sequential
from doeff_validation import (
    CheckError,
    CheckFailure,
    CheckSpec,
    ValidationException,
    check,
    perform,
    validate,
)

from doeff import EffectGenerator, do, run


class Reason(Enum):
    KEY = "key"
    PHASE = "phase"
    SEATS = "seats"


ENV = {"seats": 3, "phase": "pending"}


def _run(program: object, traverse_handler: Callable[[object], object] | None = None) -> object:
    """検査の Program を、reader と traverse の handler(既定は逐次)の下で走らせる。"""
    handler = traverse_handler if traverse_handler is not None else sequential()
    return run(scheduled(reader(ENV)(handler(program))))


def _failures(program: object, traverse_handler: Callable[[object], object] | None = None) -> ValidationException:
    """ValidationException が投げられることを確かめ、その例外を返す。"""
    with pytest.raises(ValidationException) as caught:
        _run(program, traverse_handler)
    return caught.value


def test_all_checks_pass_returns_none() -> None:
    assert _run(validate(check(operator.eq, 1, 1), check(operator.lt, 1, 2))) is None


def test_collects_every_failure_in_item_order() -> None:
    error = _failures(
        validate(
            check(operator.eq, "k-1", "k-2", reason=Reason.KEY),
            check(operator.eq, 1, 1),
            check(operator.eq, "running", "pending", reason=Reason.PHASE),
        )
    )
    assert error.reasons == [Reason.KEY, Reason.PHASE]
    first = error.failures[0]
    assert isinstance(first, CheckFailure)
    assert [(a.source, a.value) for a in first.arguments] == [("'k-1'", "k-1"), ("'k-2'", "k-2")]
    assert "2 件の検査が落ちました" in str(error)


@pytest.mark.parametrize("handler_factory", [sequential, lambda: parallel(4)])
def test_sequential_and_parallel_collect_the_same_failures(handler_factory) -> None:
    error = _failures(
        validate(
            check(operator.eq, perform(Ask("seats")), 0, reason=Reason.SEATS),
            check(operator.eq, perform(Ask("phase")), "running", reason=Reason.PHASE),
        ),
        handler_factory(),
    )
    assert error.reasons == [Reason.SEATS, Reason.PHASE]


def test_fail_fast_handler_stops_at_first_failure() -> None:
    error = _failures(
        validate(
            check(operator.eq, 1, 2, reason=Reason.KEY),
            check(operator.eq, 3, 4, reason=Reason.PHASE),
        ),
        parallel_fail_fast(1),
    )
    assert error.reasons == [Reason.KEY]


def test_performed_argument_is_run_inside_the_item_and_its_result_recorded() -> None:
    error = _failures(validate(check(operator.eq, perform(Ask("seats")), 0, reason=Reason.SEATS)))
    failure = error.failures[0]
    assert isinstance(failure, CheckFailure)
    assert failure.arguments[0].value == 3


def test_unmarked_program_argument_is_compared_as_a_value() -> None:
    effect = Ask("seats")
    # 印の無い引数は実行しない: effect の値そのものを比べる。
    assert _run(validate(check(operator.is_, effect, effect))) is None
    error = _failures(validate(check(operator.eq, effect, 3)))
    assert error.failures[0].arguments[0].value is effect


def test_failing_evaluation_is_collected_as_check_error_with_other_items_still_run() -> None:
    error = _failures(
        validate(
            check(operator.eq, perform(Ask("missing")), 0, reason=Reason.SEATS),
            check(operator.eq, 1, 2, reason=Reason.KEY),
        )
    )
    first, second = error.failures
    assert isinstance(first, CheckError)
    assert isinstance(first.error, KeyError)
    assert isinstance(second, CheckFailure)


def test_nested_validate_counts_as_one_failure_holding_its_own_failures() -> None:
    @do
    def phase_checks() -> EffectGenerator[None]:
        yield validate(
            check(operator.eq, perform(Ask("phase")), "running", reason=Reason.PHASE),
            check(operator.eq, 1, 2, reason=Reason.KEY),
        )

    error = _failures(validate(phase_checks(), check(operator.eq, 5, 6, reason=Reason.SEATS)))
    nested, own = error.failures
    assert isinstance(nested, ValidationException)
    assert nested.reasons == [Reason.PHASE, Reason.KEY]
    assert own.reason is Reason.SEATS


def test_check_alone_is_not_a_program() -> None:
    spec = check(operator.eq, 1, 2)
    assert isinstance(spec, CheckSpec)

    @do
    def misuse() -> EffectGenerator[None]:
        yield spec

    with pytest.raises(Exception):  # noqa: B017, PT011 - the VM rejects a non-program yield; its type is VM-internal
        _run(misuse())


def test_validate_rejects_items_that_are_neither_check_nor_program() -> None:
    with pytest.raises(TypeError, match="項目は check か Program"):
        validate(True)


def test_predicate_returning_a_program_is_reported_not_silently_passed() -> None:
    error = _failures(validate(check(lambda _x: Ask("seats"), 1)))
    failure = error.failures[0]
    assert isinstance(failure, CheckError)
    assert isinstance(failure.error, TypeError)


def test_unexpected_exception_in_program_item_is_raised_not_collected() -> None:
    @do
    def broken() -> EffectGenerator[None]:
        raise RuntimeError("bug")
        yield  # pragma: no cover - makes this a generator

    with pytest.raises(RuntimeError, match="bug"):
        _run(validate(broken(), check(operator.eq, 1, 2)))
