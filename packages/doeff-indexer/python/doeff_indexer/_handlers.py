"""The handler rust_indexer that answers FindSymbols with the Rust indexer (the inner extension doeff_indexer._native).

Only this module loads the extension's .so (the effect and the answer type are in _effects).
The Rust indexer reads all Python files under the current dir to build the index (module_path does not decide the index root -- same as the previous interface).
"""

from doeff import K, Transfer, TransferThrow, handler
from doeff_indexer import _native
from doeff_indexer._effects import FindSymbols, SymbolInfo


def _symbol(row: _native.SymbolInfo) -> SymbolInfo:
    """Turn one row of the extension's answer into the entry-point type."""
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
    """The handler that answers FindSymbols with the Rust indexer.

        It has no settings or per-call state, so it is not a factory (ADR-DOE-API-002 R1). Usage: rust_indexer(program). Each time it answers, it reads under the current dir
        to build the index, and if that fails (RuntimeError, OSError) it throws the error back to where the effect was raised.
    """
    try:
        index = _native.Indexer.for_module(effect.module_path)
    except (RuntimeError, OSError) as failed:
        return (yield TransferThrow(k, failed))
    rows = index.find_symbols(list(effect.tags), effect.symbol_type)
    return (yield Transfer(k, tuple(_symbol(row) for row in rows)))
