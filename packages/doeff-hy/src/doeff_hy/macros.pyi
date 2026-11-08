# Hand-written stub kept: macros.hy is a macro module -- this declares only the runtime helpers macro expansions call (with the deprecated overload that turns a dropped Program red); the tool's declaration of macros.hy would expose its 100+ compile-time functions, many as Incomplete.
"""Types of the runtime helpers referenced by macro expansions of doeff-hy (macros.hy is Hy, so pyright cannot read it).

The macros (defmacro) themselves are not listed here — they are used only at Hy compile time and are never called from Python.
`_guard_statement_value`: an expression placed in statement position (ADR-DOE-HY-001). If the value is a Program or an effect,
it was "created but never runs", so it hits the deprecated overload and doeff-hy-check turns it red.
The union of Program nodes (the same order as doeff's `DoExpr`) is written with type arguments — `DoExpr` is a generic union without type arguments, so
pyright strict treated it as partially unknown and reported one red per module with statements that the writer could not fix.
Nodes covariant in the answer type are taken as `object`; the invariant `Perform` and `EffectBase` are received through a type variable.
"""

from typing import TypeAlias, TypeVar, overload

from typing_extensions import deprecated

from doeff import (
    Apply,
    EffectBase,
    Expand,
    GetBoundaries,
    GetExecutionContext,
    GetHandlers,
    GetOuterHandlers,
    GetTraceback,
    Pass,
    Perform,
    Pure,
    Resume,
    ResumeThrow,
    Transfer,
    TransferThrow,
    WithHandlerType,
    WithObserveRaw,
)

_T = TypeVar("_T")
_A = TypeVar("_A")

_ProgramNode: TypeAlias = (
    Pure[object] | Perform[_A] | Resume | Transfer | Apply | Expand[object, object] | Pass
    | WithHandlerType[object] | WithObserveRaw[object] | ResumeThrow | TransferThrow
    | GetTraceback | GetExecutionContext | GetHandlers | GetBoundaries | GetOuterHandlers
    | EffectBase[_A]
)

@overload
@deprecated(
    "A Program / effect placed in statement position does not run — bind it with (<- _ ...) or make it the last expression of the body"
    " [ADR-DOE-HY-001]"
)
def _guard_statement_value(
    value: _ProgramNode[_A], owner: str, line: int, source: str
) -> _ProgramNode[_A]: ...
@overload
def _guard_statement_value(value: _T, owner: str, line: int, source: str) -> _T: ...
def _guard_performed(result: _T, label: str) -> _T: ...
def _doeff_check_program_return(value: object, message: str, mode: str) -> bool: ...
def _install_guard_globals(wrapped: _T, runtime_globals: dict[str, object] | None = None) -> _T: ...
