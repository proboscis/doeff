"""FindSymbols に Rust の索引器(内側の拡張 doeff_indexer._native)で答える handler rust_indexer。

拡張の .so を読むのはこの module だけ(effect と答えの型は _effects — 構成のレビュー lt-QHQJEWJDSAHGTT1ZASS28F9V68 の所見 A)。
Rust の索引器は、今の dir の下の Python の file を全部読んで索引を作る(module_path は索引の根を決めない — 前の口と同じ)。
"""

from doeff import K, Transfer, TransferThrow, handler
from doeff_indexer import _native
from doeff_indexer._effects import FindSymbols, SymbolInfo


def _symbol(row: _native.SymbolInfo) -> SymbolInfo:
    """拡張の答えの 1 行を入口の型にする。"""
    return SymbolInfo(
        name=row.name,
        module_path=row.module_path,
        full_path=row.full_path,
        symbol_type=row.symbol_type,
        tags=tuple(row.tags),
        line_number=row.line_number,
        file_path=row.file_path,
    )


@handler
def rust_indexer(effect: FindSymbols, k: K):
    """FindSymbols に Rust の索引器で答える handler。

    設定も呼びごとの状態も無いので工場にしない(ADR-DOE-API-002 R1)。使い方: rust_indexer(program)。答えるたびに今の dir の
    下を読んで索引を作り、作れなければ(RuntimeError・OSError)effect を出した所へ投げ返す。
    """
    try:
        index = _native.Indexer.for_module(effect.module_path)
    except (RuntimeError, OSError) as failed:
        return (yield TransferThrow(k, failed))
    rows = index.find_symbols(list(effect.tags), effect.symbol_type)
    return (yield Transfer(k, tuple(_symbol(row) for row in rows)))
