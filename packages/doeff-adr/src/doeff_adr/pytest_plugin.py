"""Pytest plugin for executable ADR Hy files."""

import fnmatch
import importlib
import importlib.util
import os
import sys
import warnings
from pathlib import Path
from typing import Any, Literal

import doeff_hy  # noqa: F401 - registers Hy import hooks
import pytest
from hy.importer import HyLoader

DEFAULT_FILE_PATTERNS = (
    "defadr_*.hy",
    "test_defadr_*.hy",
    "docs/adr/defadr_*.hy",
    "docs/adrs/defadr_*.hy",
)
IGNORED_DISCOVERY_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)
WiringMode = Literal["off", "warn", "strict"]
WIRING_MODES = frozenset({"off", "warn", "strict"})


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini(
        "doeff_adr_hy_files",
        "Glob patterns for executable ADR Hy files collected by doeff-adr.",
        type="linelist",
        default=[],
    )
    parser.addini(
        "doeff_adr_wiring",
        "How to report executable ADR files that pytest did not collect: off, warn, or strict.",
        default="warn",
    )
    parser.addoption(
        "--doeff-adr-wiring",
        choices=sorted(WIRING_MODES),
        default=None,
        help="Override doeff-adr wiring verification mode (off, warn, or strict).",
    )


def pytest_collect_file(file_path: Any, parent: pytest.Collector) -> pytest.Collector | None:
    path = _coerce_path(file_path)
    if path.suffix != ".hy":
        return None
    if not _should_collect_hy_file(path, parent.config):
        return None
    return DoeffAdrHyFile.from_parent(parent, path=path)


def pytest_collection_finish(session: pytest.Session) -> None:
    mode = _wiring_mode(session.config)
    if mode == "off":
        return
    root = Path(session.config.rootpath)
    patterns = _file_patterns(session.config)
    executable_adrs = _discover_executable_adrs(root, patterns)
    collected_files = {Path(item.path).resolve() for item in session.items}
    uncollected_adrs = sorted(executable_adrs - collected_files)
    if not uncollected_adrs:
        return
    message = _wiring_message(root, uncollected_adrs, mode)
    if mode == "strict":
        raise pytest.UsageError(message)
    warnings.warn(pytest.PytestWarning(message), stacklevel=1)


class DoeffAdrHyFile(pytest.File):
    def collect(self) -> Any:
        module = _import_hy_file(self.path, self.config.rootpath)
        for name in sorted(attr for attr in dir(module) if attr.startswith("test_")):
            callobj = getattr(module, name)
            if callable(callobj):
                yield pytest.Function.from_parent(self, name=name, callobj=callobj)


def _coerce_path(path: Any) -> Path:
    if isinstance(path, Path):
        return path
    strpath = getattr(path, "strpath", None)
    if strpath is not None:
        return Path(strpath)
    return Path(str(path))


def _should_collect_hy_file(path: Path, config: pytest.Config) -> bool:
    root = Path(config.rootpath)
    patterns = _file_patterns(config)
    return _matches_file_patterns(path, root, patterns)


def _file_patterns(config: pytest.Config) -> tuple[str, ...]:
    return (*DEFAULT_FILE_PATTERNS, *config.getini("doeff_adr_hy_files"))


def _matches_file_patterns(path: Path, root: Path, patterns: tuple[str, ...]) -> bool:
    rel = _relative_posix(path, root)
    candidates = {path.name, rel, path.as_posix()}
    return any(
        fnmatch.fnmatch(candidate, pattern) for pattern in patterns for candidate in candidates
    )


def _wiring_mode(config: pytest.Config) -> WiringMode:
    command_line_mode = config.getoption("doeff_adr_wiring")
    configured_mode = command_line_mode or config.getini("doeff_adr_wiring")
    if configured_mode == "off":
        return "off"
    if configured_mode == "warn":
        return "warn"
    if configured_mode == "strict":
        return "strict"
    choices = ", ".join(sorted(WIRING_MODES))
    raise pytest.UsageError(f"doeff_adr_wiring must be one of {choices}; got {configured_mode!r}")


def _discover_executable_adrs(root: Path, patterns: tuple[str, ...]) -> set[Path]:
    executable_adrs: set[Path] = set()
    for directory, directory_names, file_names in os.walk(root):
        directory_names[:] = sorted(
            name for name in directory_names if name not in IGNORED_DISCOVERY_DIRECTORIES
        )
        for file_name in sorted(file_names):
            path = Path(directory, file_name)
            if path.suffix == ".hy" and _matches_file_patterns(path, root, patterns):
                executable_adrs.add(path.resolve())
    return executable_adrs


def _wiring_message(root: Path, paths: list[Path], mode: WiringMode) -> str:
    outcome = "failed" if mode == "strict" else "warning"
    rendered_paths = "\n".join(f"  - {_relative_posix(path, root)}" for path in paths)
    return (
        f"doeff-adr wiring verification {outcome}: executable ADR files exist but were not "
        f"collected:\n{rendered_paths}\n"
        "Add their directories to pytest testpaths or the CI pytest arguments. "
        "Use doeff_adr_wiring=off only for an intentional opt-out."
    )


def _relative_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _import_hy_file(path: Path, root: Path) -> Any:
    root = root.resolve()
    path = path.resolve()
    module_name = _module_name_for_path(path, root)
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    _ensure_macro_module_loaded()
    existing = sys.modules.get(module_name)
    if existing is not None and Path(getattr(existing, "__file__", "")).resolve() == path:
        return existing
    importlib.invalidate_caches()
    loader = HyLoader(module_name, str(path))
    spec = importlib.util.spec_from_file_location(module_name, path, loader=loader)
    if spec is None:
        raise ImportError(f"could not create import spec for executable ADR: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


def _ensure_macro_module_loaded() -> None:
    if "doeff_adr.macros" in sys.modules:
        return
    path = Path(__file__).with_name("macros.hy").resolve()
    loader = HyLoader("doeff_adr.macros", str(path))
    spec = importlib.util.spec_from_file_location(
        "doeff_adr.macros",
        path,
        loader=loader,
    )
    if spec is None:
        raise ImportError(f"could not create import spec for doeff_adr macros: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["doeff_adr.macros"] = module
    loader.exec_module(module)


def _module_name_for_path(path: Path, root: Path) -> str:
    try:
        relative = path.with_suffix("").relative_to(root)
    except ValueError as exc:
        raise ValueError(f"executable ADR file is outside pytest root: {path}") from exc
    parts = relative.parts
    bad_parts = [part for part in parts if not part.isidentifier()]
    if bad_parts:
        raise ValueError(
            "executable ADR Hy files must have importable module path parts: "
            f"{path} contains {bad_parts!r}"
        )
    return ".".join(parts)
