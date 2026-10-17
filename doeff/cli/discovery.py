"""Discovery services for automatic interpreter and environment resolution.

Uses doeff-indexer to find `# doeff: interpreter, default` and
`# doeff: default` markers in the module hierarchy.
"""

import importlib
import inspect
import logging
from typing import Any, Protocol, TypeAlias, cast

from doeff.cli.profiling import profile
from doeff.cli.run_services import import_symbol
from doeff.program import Expand, Pure

EnvProgram: TypeAlias = "Pure[dict[Any, Any]] | Expand[dict[Any, Any]]"

logger: logging.Logger = logging.getLogger(__name__)


class InterpreterDiscovery(Protocol):
    def find_default_interpreter(self, program_path: str) -> str | None: ...
    def validate_interpreter(self, func: Any) -> bool: ...


class EnvDiscovery(Protocol):
    def discover_default_envs(self, program_path: str) -> list[str]: ...


class EnvMerger(Protocol):
    def merge_envs(self, env_sources: list[str]) -> EnvProgram: ...


class IndexedSymbol(Protocol):
    """A symbol the indexer found: the module it is in and its dotted path."""

    @property
    def module_path(self) -> str: ...
    @property
    def full_path(self) -> str: ...


class ModuleIndex(Protocol):
    """The index of one module hierarchy (``doeff_indexer.Indexer.for_module``)."""

    def find_symbols(self, *, tags: list[str], symbol_type: str) -> list[IndexedSymbol]: ...


class IndexerClass(Protocol):
    """The part of ``doeff_indexer.Indexer`` discovery uses (the extension ships no stub)."""

    def for_module(self, module_path: str) -> ModuleIndex: ...


class SymbolLoader(Protocol):
    def load_symbol(self, full_path: str) -> object: ...


class StandardSymbolLoader:
    def load_symbol(self, full_path: str) -> object:
        """Load the env symbol a discovered or ``--env`` path names, resolved as ``doeff run`` resolves any path."""
        with profile(f"Load symbol {full_path}", indent=2):
            return import_symbol(full_path)


class IndexerBasedDiscovery:
    def __init__(self, symbol_loader: SymbolLoader | None = None):
        with profile("Import doeff_indexer", indent=1):
            try:
                indexer_module = cast(Any, importlib.import_module("doeff_indexer"))
                self.indexer_class: IndexerClass = indexer_module.Indexer
            except ImportError as e:
                raise ImportError(
                    "doeff-indexer not found. Install with: pip install doeff-indexer"
                ) from e
        self.symbol_loader: SymbolLoader = symbol_loader or StandardSymbolLoader()

    def find_default_interpreter(self, program_path: str) -> str | None:
        with profile("Find default interpreter", indent=1):
            module_path = self._extract_module_path(program_path)
            with profile("Create indexer", indent=2):
                try:
                    indexer = self.indexer_class.for_module(module_path)
                except RuntimeError as e:
                    logger.warning("Failed to create indexer for %s: %s", module_path, e)
                    return None

            with profile("Find interpreter symbols", indent=2):
                symbols = indexer.find_symbols(
                    tags=["interpreter", "default"], symbol_type="function"
                )

            if not symbols:
                return None
            hierarchy = self._get_module_hierarchy(module_path)
            candidates = [s for s in symbols if s.module_path in hierarchy]
            if not candidates:
                return None
            closest = max(candidates, key=lambda s: hierarchy.index(s.module_path))
            return closest.full_path

    def discover_default_envs(self, program_path: str) -> list[str]:
        with profile("Find default environments", indent=1):
            module_path = self._extract_module_path(program_path)
            with profile("Create indexer", indent=2):
                try:
                    indexer = self.indexer_class.for_module(module_path)
                except RuntimeError as e:
                    logger.warning("Failed to create indexer for %s: %s", module_path, e)
                    return []

            hierarchy = self._get_module_hierarchy(module_path)
            with profile("Find env symbols", indent=2):
                all_symbols = indexer.find_symbols(tags=["default"], symbol_type="variable")

            env_paths = []
            for module in hierarchy:
                module_envs = [s.full_path for s in all_symbols if s.module_path == module]
                env_paths.extend(module_envs)
            return env_paths

    def validate_interpreter(self, func: Any) -> bool:
        if not callable(func):
            return False
        try:
            sig = inspect.signature(func)
            params = list(sig.parameters.values())
            positional = [
                p for p in params
                if p.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            if len(positional) != 1:
                return False
            return not inspect.iscoroutinefunction(func)
        except (ValueError, TypeError):
            return False

    def _extract_module_path(self, full_path: str) -> str:
        parts = full_path.split(".")
        return ".".join(parts[:-1]) if len(parts) > 1 else ""

    def _get_module_hierarchy(self, module_path: str) -> list[str]:
        if not module_path:
            return []
        parts = module_path.split(".")
        return [".".join(parts[:i]) for i in range(1, len(parts) + 1)]


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
