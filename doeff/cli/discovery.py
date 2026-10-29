"""Discovery of ``doeff run``'s default interpreter and environments.

The markers ``# doeff: interpreter, default`` and ``# doeff: default`` on the program's module
hierarchy are read from doeff-indexer's index. The discovery is a program: it yields doeff-indexer's
effect ``FindSymbols``, and ``doeff run`` runs it under the handler ``rust_indexer``, which answers
with the Rust indexer (``doeff/cli/run_services.py``). A test answers ``FindSymbols`` with its own
handler. doeff-indexer is optional: ``doeff run`` discovers only when it is installed.
"""

import logging
from collections.abc import Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias

from doeff.cli.profiling import profile
from doeff.cli.run_services import import_symbol
from doeff.do import do
from doeff.program import Expand, Pure

if TYPE_CHECKING:
    from doeff_indexer import FindSymbols, SymbolInfo

EnvProgram: TypeAlias = "Pure[dict[Any, Any]] | Expand[dict[Any, Any]]"

logger: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Discovered:
    """What discovery found for one program path."""

    #: The closest marked interpreter on the program's module hierarchy (None when there is none).
    interpreter: str | None
    #: The marked envs on the program's module hierarchy, root first (the merge order).
    envs: tuple[str, ...]


def module_hierarchy(module_path: str) -> list[str]:
    """A module path's prefixes, root first: ``a.b.c`` → ``["a", "a.b", "a.b.c"]`` (``[]`` for ``""``)."""
    if not module_path:
        return []
    parts = module_path.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts) + 1)]


def _module_of(program_path: str) -> str:
    """The module a dotted program path names its program in (``""`` for a bare name)."""
    parts = program_path.split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else ""


def _marked(
    module_path: str, tags: tuple[str, ...], symbol_type: Literal["function", "variable"]
) -> "FindSymbols":
    """The effect asking the index for the symbols with every tag (its handler raises RuntimeError
    or OSError into the program when the index cannot be built).

    Imported here, not at the top: ``doeff run`` reads this module for ``StandardEnvMerger`` without
    doeff-indexer installed. ``FindSymbols`` is pure Python (no Rust extension is read).
    """
    from doeff_indexer import FindSymbols

    return FindSymbols(module_path, tags, symbol_type)


@do
def find_default_interpreter(program_path: str) -> Generator[Any, Any, str | None]:
    """The marked interpreter (``# doeff: interpreter, default``) closest to the program on its
    module hierarchy, or None."""
    module_path = _module_of(program_path)
    hierarchy = module_hierarchy(module_path)
    symbols: tuple[SymbolInfo, ...] = yield _marked(
        module_path, ("interpreter", "default"), "function"
    )
    candidates = [symbol for symbol in symbols if symbol.module_path in hierarchy]
    if not candidates:
        return None
    return max(candidates, key=lambda symbol: hierarchy.index(symbol.module_path)).full_path


@do
def discover_default_envs(program_path: str) -> Generator[Any, Any, tuple[str, ...]]:
    """The marked envs (``# doeff: default`` on a variable) on the program's module hierarchy,
    root first (the merge order)."""
    module_path = _module_of(program_path)
    symbols: tuple[SymbolInfo, ...] = yield _marked(module_path, ("default",), "variable")
    return tuple(
        symbol.full_path
        for module in module_hierarchy(module_path)
        for symbol in symbols
        if symbol.module_path == module
    )


@do
def discover(program_path: str, *, envs: bool = True) -> Generator[Any, Any, Discovered]:
    """The default interpreter of a program path and, when ``envs``, its default envs (``doeff run``
    asks for the envs only when no ``--env`` names them).

    An index that cannot be built (the handler raises ``RuntimeError`` or ``OSError`` into the
    program) is logged as a warning and discovers nothing, so ``doeff run`` falls back to its
    default interpreter — the one place that decides the fallback.
    """
    try:
        with profile("Find default interpreter", indent=1):
            interpreter: str | None = yield find_default_interpreter(program_path)
        if not envs:
            return Discovered(interpreter, ())
        with profile("Find default environments", indent=1):
            found: tuple[str, ...] = yield discover_default_envs(program_path)
    except (RuntimeError, OSError) as failed:
        logger.warning("Failed to index %s for discovery: %s", program_path, failed)
        return Discovered(None, ())
    return Discovered(interpreter, found)


class EnvMerger(Protocol):
    def merge_envs(self, env_sources: list[str]) -> EnvProgram: ...


class SymbolLoader(Protocol):
    def load_symbol(self, full_path: str) -> object: ...


class StandardSymbolLoader:
    def load_symbol(self, full_path: str) -> object:
        """Load the env symbol a discovered or ``--env`` path names, resolved as ``doeff run`` resolves any path."""
        with profile(f"Load symbol {full_path}", indent=2):
            return import_symbol(full_path)


class StandardEnvMerger:
    def __init__(self, symbol_loader: SymbolLoader | None = None):
        self.symbol_loader: SymbolLoader = symbol_loader or StandardSymbolLoader()

    def merge_envs(self, env_sources: list[str]) -> EnvProgram:
        """Merge environment sources left-to-right. Later overrides earlier."""
        with profile("Merge environments", indent=1):
            from doeff.program import Pure

            if not env_sources:
                return Pure({})

            with profile(f"Load {len(env_sources)} env sources", indent=2):
                loaded_envs = [self.symbol_loader.load_symbol(path) for path in env_sources]

            from doeff import merge_dicts

            return merge_dicts(*loaded_envs)
