"""doeff-hy の macro の展開が参照する実行時の補助の型(macros.hy は Hy なので pyright が読めない)。

macro(defmacro)そのものはここに載せない — Hy の compile の時にだけ使い、Python からは呼ばない。
`_guard_statement_value`: 文の位置に置いた式(ADR-DOE-HY-001)。値が Program か effect なら
「作ったが走らない」ので deprecated の overload に当たり、doeff-hy-check が赤にする。
Program の node の和(doeff の `DoExpr` と同じ並び)は型引数を付けて書く — `DoExpr` は型引数の無い総称の和で
pyright の strict が partially unknown とし、文を持つ module ごとに書き手に直せない赤 1 件を出していた
(agora-redesign #2214)。答えの型で共変な node は `object`、不変な `Perform`・`EffectBase` は型変数で受ける。
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
    "文の位置に置いた Program / effect は走らない — (<- _ ...) で束縛するか、本体の最後の式にする"
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
