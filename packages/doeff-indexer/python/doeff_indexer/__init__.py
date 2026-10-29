"""doeff の印(# doeff: interpreter, default など)の静的な索引。

Python の口は effect FindSymbols と、Rust の索引器で答える handler rust_indexer、答えの 1 行の型 SymbolInfo
(ADR-DOE-API-001・card ki-1e51e218527e)。Rust の索引器は内側の拡張 doeff_indexer._native。CLI コマンド doeff-indexer は
同梱の binary を起こす(_cli)。

名は最初に使う時に読む: CLI コマンドの入口 doeff_indexer._cli はこの package を読むので、doeff と拡張を CLI の起動で読まない。
拡張を読むのは rust_indexer(_handlers)だけで、FindSymbols と SymbolInfo(_effects)は拡張の .so 無しに読める。
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_indexer._effects import FindSymbols as FindSymbols
    from doeff_indexer._effects import SymbolInfo as SymbolInfo
    from doeff_indexer._handlers import rust_indexer as rust_indexer

#: 公開の名と、それを定義する内側の module。
_DEFINED_IN = {"FindSymbols": "_effects", "SymbolInfo": "_effects", "rust_indexer": "_handlers"}
_PUBLIC_API_NAMES = tuple(_DEFINED_IN)


def __getattr__(name: str) -> object:
    """宣言した公開名だけを遅延解決する。import の失敗は呼び手へそのまま伝える。"""
    if name not in _DEFINED_IN:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(f"{__name__}.{_DEFINED_IN[name]}"), name)


def __dir__() -> list[str]:
    return sorted({*globals(), *_PUBLIC_API_NAMES})
