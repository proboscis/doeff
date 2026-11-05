"""The effect FindSymbols that queries the index, and SymbolInfo, the one-row type of its answer (does not load the Rust extension -- the handler that answers is in _handlers).

Decision: make it an effect (
ADR-DOE-API-001). The previous interface, class Indexer (Indexer.for_module(m).find_symbols(...)), is kept in the inner extension
doeff_indexer._native and is not exposed from the entry point. The one-row answer type SymbolInfo is exposed from the entry point as the result type of the R2 effect.

This module is pure Python and does not load the extension's .so: a test can answer FindSymbols with its own handler and run the same program without the Rust indexer, the extension, or
any files.
"""

from dataclasses import dataclass
from typing import Literal

from doeff import EffectBase


@dataclass(frozen=True)
class SymbolInfo:
    """One row of the index: a function or variable carrying a marker (# doeff: ... or doeff: in the docstring)."""

    #: Name (e.g. "auth_interpreter")
    name: str
    #: Module where the name lives (e.g. "myapp.features.auth")
    module_path: str
    #: Path joining module and name (e.g. "myapp.features.auth.auth_interpreter")
    full_path: str
    #: "function"・"async_function"・"variable"
    symbol_type: Literal["function", "async_function", "variable"]
    #: Marker words (e.g. ("interpreter", "default"))
    tags: tuple[str, ...]
    #: Line of the definition (from 1)
    line_number: int
    #: File of the definition (path from the index root)
    file_path: str


@dataclass(frozen=True)
class FindSymbols(EffectBase[tuple[SymbolInfo, ...]]):
    """Look up marked names in a module hierarchy: yield FindSymbols("myapp.features.auth", tags=("interpreter", "default"),
    symbol_type="function") → tuple[SymbolInfo, ...]。

        tags matches only names that have all of them (case-insensitive); if symbol_type is None, any kind is accepted. If the index cannot be built, the
        handler throws RuntimeError or OSError back to where the effect was raised.
    """

    module_path: str
    tags: tuple[str, ...] = ()
    symbol_type: Literal["function", "async_function", "variable"] | None = None
