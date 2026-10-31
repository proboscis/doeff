"""The analysis as effects, and the handler that answers them (ADR-DOE-API-001 R24).

A Program asks what a Program, a handler or an env does with the effects ``AnalyzeProgram`` /
``AnalyzeHandler`` / ``AnalyzeEnv`` / ``CheckCoverage``, and ``effect_analysis()`` makes the handler
that answers them by reading the sources (nothing is run)::

    report = yield AnalyzeProgram("pkg.jobs:job", bindings={"foundation": production_foundation})
    coverage = yield CheckCoverage(report, fold=FoldCarried(declared=True))
    assert coverage.complete, coverage.gaps

    run(effect_analysis()(program))

Decided: make them effects, and show the answer types.  The functions that answer them
(``_program_effects.analyze_program``, ``_handler_effects.check_coverage`` …) are inside the package.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import FunctionType
from typing import assert_never

from doeff import EffectBase, K, Transfer, TransferThrow, handler
from doeff.program import ProgramHandler
from doeff_effect_analyzer._handler_effects import (
    Coverage,
    analyze_env,
    analyze_handler,
    check_coverage,
)
from doeff_effect_analyzer._program_effects import (
    HandlerEffects,
    ProgramEffects,
    SuppliedDeclaration,
    analyze_program,
    qualified_name,
    resolve_target,
    runs_where_performed,
)
from doeff_effect_analyzer._result_cache import cached_result, importable_name


@dataclass(frozen=True)
class AnalyzeProgram(EffectBase[ProgramEffects]):
    """The effects a Program function performs, following its calls: ``yield AnalyzeProgram(f)`` →
    ``ProgramEffects``.

    ``target`` = the Program function (``@do`` / ``defk`` / a generator function), a
    ``functools.partial`` of one, or ``"module:attr"`` (the module is imported to resolve names).
    ``bindings`` binds its parameters to known values (``{"foundation": production_foundation}``) so
    what the body does with them is read; a name that is not a parameter is refused (``ValueError``)."""

    target: object
    #: The target's keyword arguments (parameter name → value), as the call would pass them — a map
    #: because its keys are the target's own parameter names, not fields known here.
    bindings: Mapping[str, object] | None = None


@dataclass(frozen=True)
class AnalyzeHandler(EffectBase[HandlerEffects]):
    """What a handler answers and what its clauses perform: ``yield AnalyzeHandler(h)`` →
    ``HandlerEffects``.  ``target`` = a handler value, factory or dispatcher, or ``"module:attr"``;
    ``name`` = the name it is reported by (default: its own)."""

    target: object
    name: str | None = None


@dataclass(frozen=True)
class AnalyzeEnv(EffectBase[tuple[HandlerEffects, ...]]):
    """The handlers an env builder returns, outermost first: ``yield AnalyzeEnv(builder)`` →
    ``tuple[HandlerEffects, ...]`` (to pass as ``CheckCoverage.env``).  ``bindings`` binds the
    builder's parameters."""

    builder: object
    #: The builder's keyword arguments (parameter name → value) — as ``AnalyzeProgram.bindings``.
    bindings: Mapping[str, object] | None = None


@dataclass(frozen=True)
class FoldCarried:
    """Which carried Programs (the Program a ``Spawn`` / ``Try`` … effect carries) count where they
    are performed — under the handlers around the performing site — instead of elsewhere.

    ``declared`` = the carriers that declare their handler runs the carried Program there
    (``__doeff_runs_carried__``: ``Try`` / ``Local`` / ``Listen`` / ``Spawn`` / ``SqlTransaction``);
    ``names`` = carriers named by their name or qualified name as well — an effect class, or a
    helper Program function the Program is handed to (``CarriedProgram``).  ``FoldCarried()``
    folds none in (a carried Program runs elsewhere).  It is also the predicate
    ``ProgramEffects.residual_with`` takes: ``report.residual_with(FoldCarried(declared=True))``."""

    declared: bool = False
    names: tuple[str, ...] = ()

    def __call__(self, carrier: object) -> bool:
        """Whether ``carrier``'s carried Program is counted where it is performed (the fold's rule)."""
        if self.declared and runs_where_performed(carrier):
            return True
        return isinstance(carrier, type | FunctionType) and (
            carrier.__name__ in self.names or qualified_name(carrier) in self.names
        )


@dataclass(frozen=True)
class CheckCoverage(EffectBase[Coverage]):
    """Whether ``env`` (outermost first) and the handlers ``program`` installs itself answer every
    effect it performs: ``yield CheckCoverage(report)`` → ``Coverage`` (``complete`` = no gap, every
    handler on the way read and every place followed).

    ``program`` = a ``ProgramEffects`` read before (``AnalyzeProgram``), or a Program function /
    ``functools.partial`` / ``"module:attr"`` read here with ``bindings`` — only this form can be
    kept by the result cache (a ``ProgramEffects`` names its Program by a label only).  ``env`` =
    handlers read before (``AnalyzeHandler`` / ``AnalyzeEnv``).  ``fold`` = the carried Programs
    counted where they are performed.  ``supplied`` = declarations for handlers that cannot be read:
    such a handler is read from its declaration where it is placed, and is not named in
    ``unknown_handlers``."""

    program: ProgramEffects | Callable[..., object] | str
    env: tuple[HandlerEffects, ...] = ()
    #: ``program``'s keyword arguments when it is read here — as ``AnalyzeProgram.bindings``.
    bindings: Mapping[str, object] | None = None
    fold: FoldCarried = FoldCarried()
    supplied: tuple[SuppliedDeclaration, ...] = ()

    def __post_init__(self) -> None:
        """Refuse bindings for a Program read before: they would bind nothing, silently."""
        if isinstance(self.program, ProgramEffects) and self.bindings is not None:
            raise TypeError(
                "CheckCoverage: bindings bind a Program function read here; "
                f"{self.program.target} was read already"
            )


#: What a target that cannot be analyzed raises (a name that does not import or resolve, a value that
#: is not a Program function or a handler, a binding that is not a parameter) — thrown back to the
#: place that performed the effect.
_REFUSALS = (TypeError, ValueError, ImportError, AttributeError)


def effect_analysis(*, result_cache: bool = False) -> ProgramHandler:
    """The handler that answers ``AnalyzeProgram`` / ``AnalyzeHandler`` / ``AnalyzeEnv`` /
    ``CheckCoverage`` from the sources (nothing is run; Hy is macro-expanded first).  A target that
    cannot be analyzed is thrown back where the effect was performed.

    ``result_cache=True`` keeps the answer of a ``CheckCoverage`` of a Program function read here
    (no ``env``; the function and its binding values found again by their import names — the key
    also names the fold and the supplied declarations) on disk, reused while every module file it
    depends on has the same content (``DOEFF_EFFECT_ANALYZER_RESULT_CACHE=off`` turns it off).  Any
    other effect is read every time.  An answer that cannot be stored is answered all the same.

    Use: ``effect_analysis()(program)`` / ``effect_analysis(result_cache=True)(program)``.
    """

    # One clause per effect, written in the body itself: the analyzer reads a handler by its
    # clauses, this one included (a closure check of a Program that installs it reads it).
    @handler
    def answer_effect_analysis(
        effect: AnalyzeProgram | AnalyzeHandler | AnalyzeEnv | CheckCoverage, k: K
    ):
        answer: object
        try:
            match effect:
                case AnalyzeProgram(target=target, bindings=bindings):
                    answer = analyze_program(target, bindings=bindings)
                case AnalyzeHandler(target=target, name=name):
                    answer = analyze_handler(target, name=name)
                case AnalyzeEnv(builder=builder, bindings=bindings):
                    answer = tuple(analyze_env(builder, bindings=bindings))
                case CheckCoverage():
                    answer = _coverage(effect, result_cache=result_cache)
                case _:
                    assert_never(effect)
        except _REFUSALS as refused:
            return (yield TransferThrow(k, refused))
        return (yield Transfer(k, answer))

    return answer_effect_analysis


def _coverage(effect: CheckCoverage, *, result_cache: bool) -> Coverage:
    """A coverage check, from the result cache when asked for and its arguments can be named."""

    def compute() -> Coverage:
        """Read the Program (unless read before) and run its effects through the env."""
        report = (
            effect.program
            if isinstance(effect.program, ProgramEffects)
            else analyze_program(effect.program, bindings=effect.bindings)
        )
        return check_coverage(report, effect.env, include=effect.fold, supplied=effect.supplied)

    if not result_cache or isinstance(effect.program, ProgramEffects) or effect.env:
        return compute()
    target = resolve_target(effect.program) if isinstance(effect.program, str) else effect.program
    bound = dict(effect.bindings or {})
    return cached_result(
        _identity(target, bound, effect.fold, effect.supplied),
        compute,
        depends_on=(target, *bound.values()),
    )


def _identity(
    target: object,
    bound: Mapping[str, object],
    fold: FoldCarried,
    supplied: tuple[SuppliedDeclaration, ...],
) -> tuple[str, ...] | None:
    """The name of a coverage check in the result cache: the function and each binding by the name that
    finds it again, the fold and the supplied declarations (None = an argument has no such name — not
    cached)."""
    names = [importable_name(target), *(importable_name(value) for value in bound.values())]
    if any(name is None for name in names):
        return None
    return (
        "doeff-effect-analyzer.check-coverage",
        str(names[0]),
        *(f"{parameter}={name}" for parameter, name in zip(bound, names[1:], strict=True)),
        f"fold={'declared' if fold.declared else '-'}:{','.join(fold.names)}",
        *(
            "supplied={}:{}:{}".format(
                d.part,
                ",".join(map(qualified_name, d.handles)),
                ",".join(map(qualified_name, d.effects)),
            )
            for d in supplied
        ),
    )
