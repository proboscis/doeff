"""Value types of doeff-validation — the check specification (``Check``), the execution mark (``Perform``), the failure records, and
``ValidationException``.

``Check`` is a value passed to ``validate`` and is not a Program on its own (it cannot be ``yield``ed).
This expresses "check can be written only directly under validate" as a Python type.

Design record: ``docs/design/doeff-validation/design.md``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from types import BuiltinFunctionType, FunctionType, MethodType

from doeff import DoExpr, EffectBase


def _is_program(value: object) -> bool:
    """Whether the value is a Program or an effect (used to validate Perform arguments and validate items, and to detect a mix-up of verdict values)."""
    return isinstance(value, (DoExpr, EffectBase))


@dataclass(frozen=True)
class CheckArgument:
    """One argument passed to a check — the source text as written, a thunk evaluated inside the item, and the effect mark.

    ``perform`` true (Hy ``(! …)`` / Python ``Perform(…)``) means the thunk value is a Program or an effect, and
    the value from executing it inside the item is compared. If false, the thunk value is compared as-is (a Program value is not executed either).
    """

    source: str
    evaluate: Callable[[], object]
    perform: bool


@dataclass(frozen=True)
class Perform:
    """Mark in a Python Check that executes the argument as an effect inside the item (corresponds to Hy ``(! …)``).

    ``Check(operator.eq, Perform(CountSeats(node)), 0)`` — an argument without the mark is compared as-is,
    without being executed, even if it is a Program value.
    """

    program: object

    def __post_init__(self) -> None:
        if not _is_program(self.program):
            raise TypeError(
                f"Perform: pass a Program or an effect — {type(self.program).__name__}"
            )


def _callable_name(fn: Callable[..., object]) -> str:
    """Name of the predicate, used in the source text of a Check expression."""
    if isinstance(fn, (FunctionType, BuiltinFunctionType, MethodType)):
        return fn.__name__
    return repr(fn)


def _check_argument(value: object) -> CheckArgument:
    """Turn a Check argument into a CheckArgument (execute it as an effect if it has the ``Perform`` mark).

    A CheckArgument is used as-is — the Hy check expansion passes a thunk that defers evaluation into the item, together with the
    source text as written, as a CheckArgument.
    """
    match value:
        case CheckArgument():
            return value
        case Perform(program=program):
            return CheckArgument(f"Perform({program!r})", lambda: program, True)
        case _:
            return CheckArgument(repr(value), lambda: value, False)


@dataclass(frozen=True, init=False)
class Check:
    """Specification of one check. It is meaningful only as an item directly under ``validate`` (not a Program on its own).

    ``Check(operator.eq, job.phase, Phase.PENDING, reason=R)`` — each argument is a plain value or
    an effect marked with ``Perform(program)``. A marked argument is executed inside the item and its result value is
    passed to ``predicate``, and the truthiness of that value is the verdict. If ``expression`` is not given, the expression text is built from the predicate name and
    the source text of each argument.
    """

    expression: str
    predicate: Callable[..., object]
    arguments: tuple[CheckArgument, ...]
    reason: object | None

    def __init__(
        self,
        predicate: Callable[..., object],
        *args: object,
        reason: object | None = None,
        expression: str | None = None,
    ) -> None:
        if not callable(predicate):
            raise TypeError(
                "Check: pass a predicate (callable) as the first argument — Check(operator.eq, a, b)"
            )
        arguments = tuple(_check_argument(a) for a in args)
        object.__setattr__(
            self,
            "expression",
            expression or f"{_callable_name(predicate)}({', '.join(a.source for a in arguments)})",
        )
        object.__setattr__(self, "predicate", predicate)
        object.__setattr__(self, "arguments", arguments)
        object.__setattr__(self, "reason", reason)


@dataclass(frozen=True)
class EvaluatedArgument:
    """One argument of a failed check — the source text as written, and the value after evaluation (the result of executing it if it had the effect mark)."""

    source: str
    value: object


@dataclass(frozen=True)
class CheckFailure:
    """Record of one check whose verdict was false (expression text, evaluated argument values, reason)."""

    expression: str
    arguments: tuple[EvaluatedArgument, ...]
    reason: object | None

    def describe(self) -> str:
        """One line of the exception message — like pytest assert, lists the expression and the values of each argument."""
        values = "  ".join(f"{arg.source} = {arg.value!r}" for arg in self.arguments)
        reason = f"  [{self.reason}]" if self.reason is not None else ""
        return f"{self.expression}{'  ' + values if values else ''}{reason}"


@dataclass(frozen=True)
class CheckError:
    """Record of one check that failed because evaluating an argument or the verdict raised an exception (collected as that item's failure).

    ``evaluated`` holds the arguments that could be evaluated before the failure.
    """

    expression: str
    evaluated: tuple[EvaluatedArgument, ...]
    error: Exception
    reason: object | None

    def describe(self) -> str:
        """One line of the exception message — lists the expression and the exception raised midway through evaluation."""
        values = "  ".join(f"{arg.source} = {arg.value!r}" for arg in self.evaluated)
        reason = f"  [{self.reason}]" if self.reason is not None else ""
        return (
            f"{self.expression}{'  ' + values if values else ''}"
            f"  evaluation failed: {type(self.error).__name__}: {self.error}{reason}"
        )


class ValidationException(Exception):  # noqa: N818 - name specified by the operator
    """One of the ``validate`` items failed. Holds the failures of the failed items, all of them, in item order.

    The elements of ``failures`` are ``CheckFailure`` (verdict false), ``CheckError`` (evaluation raised), and nested
    ``ValidationException`` (a ``validate`` inside a Program item failed — counted as one failure). Never empty.
    """

    def __init__(
        self,
        failures: "tuple[CheckFailure | CheckError | ValidationException, ...]",
        context: str | None = None,
    ) -> None:
        """``context`` is where the check is (for a contract, e.g. ``"<function name> pre-condition"``)."""
        if not failures:
            raise ValueError("ValidationException requires at least one failure")
        self.failures = failures
        self.context = context
        super().__init__(self._message())

    @property
    def reasons(self) -> list[object | None]:
        """Reasons of the direct failures. A nested failure is that ``ValidationException`` itself."""
        return [f if isinstance(f, ValidationException) else f.reason for f in self.failures]

    def _message(self) -> str:
        """Message listing all failures one per line (nested ones are indented to list the failures inside)."""
        where = f"{self.context}: " if self.context else ""
        lines = [f"{where}{len(self.failures)} checks failed:"]
        for failure in self.failures:
            match failure:
                case ValidationException():
                    nested = str(failure).replace("\n", "\n    ")
                    lines.append(f"  nested validate: {nested}")
                case CheckFailure() | CheckError():
                    lines.append(f"  {failure.describe()}")
        return "\n".join(lines)


class ItemFailedError(Exception):
    """Signal that a check item failed (stops only that item). An internal exception used only inside ``validate``."""

    def __init__(self, failure: CheckFailure | CheckError) -> None:
        super().__init__(failure.describe())
        self.failure = failure
