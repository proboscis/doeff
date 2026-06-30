"""Pytest plugin for executable ADR Hy files."""

from __future__ import annotations

import fnmatch
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import doeff_hy  # noqa: F401 - registers Hy import hooks
import pytest
from hy.importer import HyLoader

DEFAULT_FILE_PATTERNS = (
    "defadr_*.hy",
    "test_defadr_*.hy",
    "docs/adr/defadr_*.hy",
    "docs/adrs/defadr_*.hy",
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini(
        "doeff_adr_hy_files",
        "Glob patterns for executable ADR Hy files collected by doeff-adr.",
        type="linelist",
        default=[],
    )


def pytest_collect_file(file_path: Any, parent: pytest.Collector) -> pytest.Collector | None:
    path = _coerce_path(file_path)
    if path.suffix != ".hy":
        return None
    if not _should_collect_hy_file(path, parent.config):
        return None
    return DoeffAdrHyFile.from_parent(parent, path=path)


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
    patterns = [*DEFAULT_FILE_PATTERNS, *config.getini("doeff_adr_hy_files")]
    rel = _relative_posix(path, root)
    candidates = {path.name, rel, path.as_posix()}
    return any(
        fnmatch.fnmatch(candidate, pattern)
        for pattern in patterns
        for candidate in candidates
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
