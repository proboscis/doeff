"""User-facing function of doeff-validation — ``validate``.

``validate`` is a thin layer on top of doeff-traverse: it runs a sequence of items (a check specification ``Check`` or a Program) with ``Traverse``
(sequential, concurrent, or fail-fast is decided by the traverse handler placed at the root of the assembly), and collects
the failure of each item through per-element isolation. In Hy, the macros with the same meaning are used via ``(require doeff-hy.macros [defk validate check])``.
"""

from typing import TYPE_CHECKING

from doeff_traverse import Inspect, Traverse

from doeff import EffectGenerator, do
from doeff_validation._failures import (
    Check,
    CheckArgument,
    CheckError,
    CheckFailure,
    EvaluatedArgument,
    ItemFailedError,
    ValidationException,
    _is_program,
)

if TYPE_CHECKING:
    from doeff import Program


@do
def _evaluate(argument: CheckArgument) -> EffectGenerator[object]:
    """Evaluate one argument inside the item (if it has the effect mark, execute it and use the result value)."""
    value = argument.evaluate()
    if argument.perform:
        return (yield value)
    return value


def judge(verdict: object) -> bool:
    """Turn the verdict value into a boolean. If it is a Program or an effect, raise TypeError as a mix-up (forgotten mark).

    The single point shared by validate items and the contract check of defk / do! (contract-check-block in macros.hy).
    A Program value is always truthy, so converting it to a boolean as-is would let the check pass silently.
    """
    if _is_program(verdict):
        raise TypeError(
            f"check: the verdict value is a Program ({verdict!r}) — for an effect verdict, make it an argument marked with (! …) / Perform(…)"
        )
    return bool(verdict)


@do
def _run_check(spec: Check) -> EffectGenerator[None]:
    """Run one check item: evaluate the arguments from the left (executing Programs), judge, and stop the item if it fails."""
    evaluated: list[EvaluatedArgument] = []
    try:
        for argument in spec.arguments:
            value = yield _evaluate(argument)
            evaluated.append(EvaluatedArgument(argument.source, value))
        verdict = judge(spec.predicate(*(arg.value for arg in evaluated)))
    except ItemFailedError:
        raise
    except Exception as error:  # an evaluation failure is collected as this item's failure (not discarded — recorded below and re-raised)
        raise ItemFailedError(
            CheckError(spec.expression, tuple(evaluated), error, spec.reason)
        ) from error
    if not verdict:
        raise ItemFailedError(CheckFailure(spec.expression, tuple(evaluated), spec.reason))


def _as_program(item: object) -> object:
    """The f of Traverse — turn an item into the processing of one element (a check becomes a check Program; a Program stays as-is)."""
    match item:
        case Check():
            return _run_check(item)
        case _:
            return item


@do
def _validate(items: tuple[object, ...]) -> EffectGenerator[None]:
    """Run the items with Traverse, collect failures from the per-element results, and raise an exception if there is even one."""
    try:
        collection = yield Traverse(_as_program, items, label="validate")
    except ItemFailedError as failed:
        # The fail-fast traverse handler (parallel_fail_fast) raises the first failure as-is.
        raise ValidationException((failed.failure,)) from None
    except ValidationException as nested:
        raise ValidationException((nested,)) from None
    results = yield Inspect(collection)
    failures: list[CheckFailure | CheckError | ValidationException] = []
    for result in results:
        if not result.failed:
            continue
        match result.value:
            case ItemFailedError(failure=failure):
                failures.append(failure)
            case ValidationException() as nested:
                failures.append(nested)
            case BaseException() as abnormal:
                # An anomaly inside a Program item that is not a check failure (a program bug or an unrecoverable failure) is
                # raised as-is without being collected.
                raise abnormal
            case other:
                raise TypeError(
                    f"validate: item {result.index} failed with a non-exception value {other!r}"
                )
    if failures:
        raise ValidationException(tuple(failures))


def validate(*items: "Check | Program[object]") -> "Program[None]":
    """Run all independent items and collect the failures of all failed items.

    - Items are a check specification ``Check`` or any Program. They do not depend on each other's results (they may run concurrently).
    - check item: evaluate the arguments from the left (executing those with the effect mark) and judge; if false,
        ``CheckFailure``; if evaluation raises, ``CheckError``.
    - Program item: a ``ValidationException`` raised inside is received as one failure
        (to reuse a group of checks, give the helper its own ``validate``).
    - If there is even one failure, raise ``ValidationException``. Otherwise ``None`` (this is a "verify, and raise if bad"
        statement, so no value is returned. Code that needs a value goes before or after validate).
    - Sequential, concurrent, or fail-fast is chosen by the doeff-traverse handler placed at the root of the assembly
      (``sequential`` / ``parallel(n)`` / ``parallel_fail_fast(n)``)。
    """
    for item in items:
        if not isinstance(item, Check) and not _is_program(item):
            raise TypeError(
                f"validate: an item must be a check or Program — got {type(item).__name__}"
            )
    return _validate(items)
