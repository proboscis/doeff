"""doeff-hy の macro の展開が参照する実行時の補助の型(macros.hy は Hy なので pyright が読めない)。

macro(defmacro)そのものはここに載せない — Hy の compile の時にだけ使い、Python からは呼ばない。
`_guard_statement_value`: 文の位置に置いた式(ADR-DOE-HY-001)。値が Program か effect なら
「作ったが走らない」ので deprecated の overload に当たり、doeff-hy-check が赤にする。
"""

from typing import Any, TypeVar, overload

from typing_extensions import deprecated

from doeff import DoExpr, EffectBase

_T = TypeVar("_T")

@overload
@deprecated(
    "文の位置に置いた Program / effect は走らない — (<- _ ...) で束縛するか、本体の最後の式にする"
    " [ADR-DOE-HY-001]"
)
def _guard_statement_value(value: DoExpr | EffectBase, owner: str, line: int, source: str) -> Any: ...
@overload
def _guard_statement_value(value: _T, owner: str, line: int, source: str) -> _T: ...
def _guard_performed(result: _T, label: str) -> _T: ...
def _doeff_check_program_return(value: object, message: str, mode: str) -> bool: ...
def _install_guard_globals(wrapped: _T, runtime_globals: dict[str, object] | None = None) -> _T: ...
