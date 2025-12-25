"""Discovery services for automatic interpreter and environment resolution.

This module provides protocols and implementations for discovering default
interpreters and environments based on # doeff: markers in the codebase.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, Protocol

from doeff import Program
from doeff.cli.profiling import profile

logger = logging.getLogger(__name__)

# Project root markers (in order of priority)
PROJECT_ROOT_MARKERS = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    ".git",
    ".hg",
    "requirements.txt",
)


def find_project_root(start_path: Path | str | None = None) -> Path | None:
    """Find the project root by walking up the directory tree.

    Looks for common project markers (pyproject.toml, .git, etc.) starting
    from the given path or current working directory.

    Args:
        start_path: Starting directory path. If None, uses current working directory.

    Returns:
        Path to project root if found, None otherwise.

    Example:
        >>> find_project_root()
        PosixPath('/home/user/myproject')
        >>> find_project_root('/home/user/myproject/src/app/module.py')
        PosixPath('/home/user/myproject')
    """
    if start_path is None:
        current = Path.cwd()
    else:
        current = Path(start_path)
        if current.is_file():
            current = current.parent

    current = current.resolve()

    while current != current.parent:
        for marker in PROJECT_ROOT_MARKERS:
            marker_path = current / marker
            if marker_path.exists():
                return current
        current = current.parent

    # Check root directory as well
    for marker in PROJECT_ROOT_MARKERS:
        if (current / marker).exists():
            return current

    return None


def ensure_project_root_in_sys_path(project_root: Path | None = None) -> Path | None:
    """Ensure project root is in sys.path for module imports.

    Finds the project root and adds it to sys.path if not already present.
    This allows importing modules when running from a subdirectory.

    Args:
        project_root: Optional project root path. If None, auto-discovers.

    Returns:
        The project root path if found, None otherwise.
    """
    if project_root is None:
        project_root = find_project_root()

    if project_root is None:
        return None

    project_root_str = str(project_root)

    # Add to sys.path if not already present
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)
        logger.debug("Added project root to sys.path: %s", project_root_str)

    return project_root


class InterpreterDiscovery(Protocol):
    """Protocol for discovering default interpreters."""

    def find_default_interpreter(self, program_path: str) -> str | None:
        """Find the closest default interpreter for a program path.

        Args:
            program_path: Full Python path to program (e.g., "some.module.program")

        Returns:
            Full path to interpreter function, or None if not found

        Example:
            >>> discovery.find_default_interpreter("myapp.features.auth.login")
            "myapp.features.auth.custom_interpreter"
        """
        ...

    def validate_interpreter(self, func: Any) -> bool:
        """Validate if a function is a valid interpreter.

        Args:
            func: Function to validate

        Returns:
            True if valid interpreter signature

        A valid interpreter:
        - Is callable
        - Accepts exactly one positional argument (Program)
        - Returns result (not a coroutine)
        """
        ...


class EnvDiscovery(Protocol):
    """Protocol for discovering default environments."""

    def discover_default_envs(self, program_path: str) -> list[str]:
        """Find all default environments from root to program module.

        Args:
            program_path: Full Python path to program

        Returns:
            List of env paths in hierarchy order (root → program)

        Example:
            >>> discovery.discover_default_envs("myapp.features.auth.login")
            [
                "myapp.base_env",
                "myapp.features.feature_env",
                "myapp.features.auth.auth_env"
            ]
        """
        ...


class EnvMerger(Protocol):
    """Protocol for merging multiple environment sources."""

    def merge_envs(self, env_sources: list[str]) -> Program[dict]:
        """Merge multiple env sources into a single Program[dict].

        Args:
            env_sources: List of full paths to env objects (dict or Program[dict])

        Returns:
            Program[dict] that evaluates to merged environment

        Merging strategy:
        - Later values override earlier values
        - dict sources are used directly
        - Program[dict] sources are evaluated

        Example:
            >>> merger.merge_envs([
            ...     "myapp.base_env",    # {'timeout': 10}
            ...     "myapp.override_env"  # {'timeout': 30, 'debug': True}
            ... ])
            Program[dict]  # Evaluates to {'timeout': 30, 'debug': True}
        """
        ...


class SymbolLoader(Protocol):
    """Protocol for loading Python symbols dynamically."""

    def load_symbol(self, full_path: str) -> Any:
        """Load a Python symbol by its full path.

        Args:
            full_path: Full dotted path (e.g., "module.submodule.symbol")

        Returns:
            The loaded Python object

        Raises:
            ImportError: If module cannot be imported
            AttributeError: If symbol not found in module

        Example:
            >>> loader.load_symbol("myapp.interpreters.my_interpreter")
            <function my_interpreter at 0x...>
        """
        ...


class IndexerBasedDiscovery:
    """Discovery implementation using doeff-indexer."""

    def __init__(self, symbol_loader: SymbolLoader | None = None, project_root: Path | None = None):
        """Initialize discovery with optional symbol loader and project root.

        Args:
            symbol_loader: Custom symbol loader, or None for default
            project_root: Explicit project root path, or None to auto-discover
        """
        with profile("Import doeff_indexer", indent=1):
            try:
                from doeff_indexer import Indexer
                self.indexer_class = Indexer
            except ImportError as e:
                raise ImportError(
                    "doeff-indexer not found. Ensure the package is installed (e.g., pip install "
                    "doeff) and that a Rust toolchain is available to build it."
                ) from e

        self.symbol_loader = symbol_loader or StandardSymbolLoader()

        # Discover and store project root
        with profile("Discover project root", indent=1):
            if project_root is not None:
                self._project_root = Path(project_root).resolve()
            else:
                self._project_root = find_project_root()

            if self._project_root is not None:
                ensure_project_root_in_sys_path(self._project_root)
                logger.debug("Using project root: %s", self._project_root)

    @property
    def project_root(self) -> Path | None:
        """Get the discovered project root path."""
        return self._project_root

    def _create_indexer(self, module_path: str):
        """Create an indexer, changing to project root if needed.

        Args:
            module_path: The Python module path to create indexer for

        Returns:
            Indexer instance or None if creation failed
        """
        original_cwd = None
        try:
            # Change to project root if we have one and it differs from cwd
            if self._project_root is not None:
                cwd = Path.cwd().resolve()
                if cwd != self._project_root:
                    original_cwd = cwd
                    os.chdir(self._project_root)
                    logger.debug("Changed to project root: %s", self._project_root)

            return self.indexer_class.for_module(module_path)
        finally:
            # Restore original working directory
            if original_cwd is not None:
                os.chdir(original_cwd)
                logger.debug("Restored working directory: %s", original_cwd)

    def find_default_interpreter(self, program_path: str) -> str | None:
        """Find closest default interpreter in module hierarchy.

        Searches from program module up to root, returns closest match.
        """
        with profile("Find default interpreter", indent=1):
            module_path = self._extract_module_path(program_path)

            with profile("Create indexer", indent=2):
                try:
                    indexer = self._create_indexer(module_path)
                except RuntimeError as e:
                    logger.warning(
                        "Failed to create indexer for module %s: %s. "
                        "Skipping interpreter discovery.",
                        module_path,
                        e,
                    )
                    return None

            # Find all interpreters with default marker
            with profile("Find interpreter symbols", indent=2):
                symbols = indexer.find_symbols(
                    tags=["interpreter", "default"],
                    symbol_type="function"
                )

            if not symbols:
                return None

            # Get module hierarchy for program
            hierarchy = self._get_module_hierarchy(module_path)

            # Filter symbols to only those in hierarchy
            candidates = [
                s for s in symbols
                if s.module_path in hierarchy
            ]

            if not candidates:
                return None

            # Select closest (rightmost in hierarchy)
            closest = max(candidates, key=lambda s: hierarchy.index(s.module_path))
            return closest.full_path

    def discover_default_envs(self, program_path: str) -> list[str]:
        """Find all default environments in module hierarchy.

        Returns environments in hierarchy order (root → program).
        """
        with profile("Find default environments", indent=1):
            module_path = self._extract_module_path(program_path)

            with profile("Create indexer", indent=2):
                try:
                    indexer = self._create_indexer(module_path)
                except RuntimeError as e:
                    logger.warning(
                        "Failed to create indexer for module %s: %s. "
                        "Skipping environment discovery.",
                        module_path,
                        e,
                    )
                    return []

            # Get module hierarchy
            hierarchy = self._get_module_hierarchy(module_path)

            # Find all env symbols
            with profile("Find env symbols", indent=2):
                all_symbols = indexer.find_symbols(
                    tags=["default"],
                    symbol_type="variable"
                )

            # Filter and order by hierarchy
            env_paths = []
            for module in hierarchy:
                module_envs = [
                    s.full_path for s in all_symbols
                    if s.module_path == module
                ]
                env_paths.extend(module_envs)

            return env_paths

    def validate_interpreter(self, func: Any) -> bool:
        """Validate interpreter signature."""
        import inspect

        if not callable(func):
            return False

        try:
            sig = inspect.signature(func)
            params = list(sig.parameters.values())

            # Should have exactly 1 positional parameter
            positional = [
                p for p in params
                if p.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD
                )
            ]

            if len(positional) != 1:
                return False

            # Should not be async
            return not inspect.iscoroutinefunction(func)

        except (ValueError, TypeError):
            return False

    def _extract_module_path(self, full_path: str) -> str:
        """Extract module path from full symbol path.

        Example: "some.module.a.program" → "some.module.a"
        """
        parts = full_path.split(".")
        if len(parts) == 1:
            return ""
        return ".".join(parts[:-1])

    def _get_module_hierarchy(self, module_path: str) -> list[str]:
        """Get module hierarchy from root to module.

        Example: "some.module.a" → ["some", "some.module", "some.module.a"]
        """
        if not module_path:
            return []

        parts = module_path.split(".")
        hierarchy = []
        for i in range(1, len(parts) + 1):
            hierarchy.append(".".join(parts[:i]))
        return hierarchy


class StandardEnvMerger:
    """Standard environment merging using Program composition."""

    def __init__(self, symbol_loader: SymbolLoader | None = None):
        """Initialize merger with optional symbol loader.

        Args:
            symbol_loader: Custom symbol loader, or None for default
        """
        self.symbol_loader = symbol_loader or StandardSymbolLoader()

    def merge_envs(self, env_sources: list[str]) -> Program[dict]:
        """Merge environment sources left-to-right.

        Later values override earlier values.
        """
        with profile("Merge environments", indent=1):
            from doeff import do

            if not env_sources:
                return Program.pure({})

            # Load all env sources
            with profile(f"Load {len(env_sources)} env sources", indent=2):
                loaded_envs = [self.symbol_loader.load_symbol(path) for path in env_sources]

            @do
            def merge() -> dict:
                """Merge all envs using Program composition."""
                from doeff.effects import Local
                from doeff.program import KleisliProgramCall
                from doeff.types import EffectBase

                merged: dict[str, Any] = {}

                for env_source in loaded_envs:
                    env_dict: Any
                    program_like: Program | KleisliProgramCall | EffectBase | None = None

                    if isinstance(env_source, (Program, KleisliProgramCall, EffectBase)):
                        program_like = env_source
                    elif callable(env_source):
                        result = env_source()
                        if isinstance(result, (Program, KleisliProgramCall, EffectBase)):
                            program_like = result
                        else:
                            env_dict = result
                            program_like = None
                    else:
                        env_dict = env_source
                        program_like = None

                    if program_like is not None:
                        # Provide already merged values so later envs can `ask` for them.
                        if merged:
                            env_dict = yield Local(dict(merged), program_like)
                        else:
                            env_dict = yield program_like

                    # Merge (later overrides earlier) while preserving Program/Effect values.
                    if isinstance(env_dict, dict):
                        merged.update(env_dict)
                    else:
                        merged.update(dict(env_dict))

                return merged

            return merge()


class StandardSymbolLoader:
    """Standard Python symbol loader using importlib."""

    def load_symbol(self, full_path: str) -> Any:
        """Load symbol by importing module and getting attribute.

        Args:
            full_path: Full dotted path (e.g., "module.submodule.symbol")

        Returns:
            The loaded Python object

        Raises:
            ImportError: If module cannot be imported
            AttributeError: If symbol not found

        Example:
            >>> loader.load_symbol("os.path.join")
            <function join at 0x...>
        """
        with profile(f"Load symbol {full_path}", indent=2):
            parts = full_path.split(".")

            # Try progressively longer module paths
            for i in range(len(parts), 0, -1):
                module_path = ".".join(parts[:i])
                attr_path = parts[i:]

                try:
                    module = importlib.import_module(module_path)

                    # Navigate through attributes
                    obj = module
                    for attr in attr_path:
                        obj = getattr(obj, attr)

                    return obj

                except (ImportError, AttributeError):
                    if i == 1:
                        # Last attempt failed
                        raise

            raise ImportError(f"Could not import {full_path}")
