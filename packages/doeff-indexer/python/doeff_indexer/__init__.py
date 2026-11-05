"""A static index of doeff markers (such as # doeff: interpreter, default).

The Python interface is the effect FindSymbols, the handler rust_indexer that answers with the Rust indexer, and SymbolInfo, the one-row answer type
(ADR-DOE-API-001). The Rust indexer is the inner extension doeff_indexer._native. The CLI command doeff-indexer
launches the bundled binary (_cli).

Names are loaded on first use: the CLI entry point doeff_indexer._cli imports this package, so launching the CLI does not load doeff or the extension.
Only rust_indexer (_handlers) loads the extension; FindSymbols and SymbolInfo (_effects) can be loaded without the extension's .so.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_indexer._effects import FindSymbols as FindSymbols
    from doeff_indexer._effects import SymbolInfo as SymbolInfo
    from doeff_indexer._handlers import rust_indexer as rust_indexer

#: Public names and the inner module that defines each.
_DEFINED_IN = {"FindSymbols": "_effects", "SymbolInfo": "_effects", "rust_indexer": "_handlers"}
_PUBLIC_API_NAMES = tuple(_DEFINED_IN)


def __getattr__(name: str) -> object:
    """Lazily resolve only the declared public names. An import failure is propagated to the caller as is."""
    if name not in _DEFINED_IN:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(f"{__name__}.{_DEFINED_IN[name]}"), name)


def __dir__() -> list[str]:
    return sorted({*globals(), *_PUBLIC_API_NAMES})
