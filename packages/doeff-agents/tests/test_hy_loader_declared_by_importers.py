"""Every Python module of doeff_agents that imports a Hy module loads Hy itself.

``import doeff`` no longer registers Hy's import hook as a side effect (doeff core,
2026-09-23: import floor 47 → 28 MiB). A Python module here that imports a ``.hy``
module of this package must therefore declare ``import hy`` (or reach it through a
parent package that does); otherwise it imports only when some earlier import happened
to load Hy. ``tests/conftest.py`` imports hy, so this can only be observed in a fresh
interpreter — each module below is imported in its own subprocess.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE_DIR = SRC / "doeff_agents"


def _module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _hy_module_names() -> set[str]:
    return {_module_name(path) for path in PACKAGE_DIR.rglob("*.hy")}


def _imported_names(path: Path, module: str) -> set[str]:
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            names.add(target)
            names.update(f"{target}.{alias.name}" for alias in node.names)
    return names


def _python_importers_of_hy_modules() -> list[str]:
    hy_modules = _hy_module_names()
    return [
        _module_name(path)
        for path in sorted(PACKAGE_DIR.rglob("*.py"))
        if _imported_names(path, _module_name(path)) & hy_modules
    ]


IMPORTERS = _python_importers_of_hy_modules()

# The public names are resolved lazily by doeff_agents.__getattr__; they reach
# adapters.base, which imports the Hy I/O effect vocabulary.
PUBLIC_STATEMENTS = [
    "from doeff_agents import AgentType",
    "from doeff_agents import LaunchConfig",
]


def _fresh_import(statement: str) -> subprocess.CompletedProcess[str]:
    probe = (
        "import sys\n"
        "import doeff\n"
        "assert 'hy' not in sys.modules, 'import doeff loaded hy; this probe needs a Hy-free start'\n"
        f"{statement}\n"
    )
    return subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, check=False
    )


def test_the_scan_finds_the_importers() -> None:
    # Guard against a scan that silently finds nothing (a wrong SRC path would pass every case).
    assert "doeff_agents.adapters.base" in IMPORTERS
    assert "doeff_agents.claude_home" in IMPORTERS


@pytest.mark.parametrize("module", IMPORTERS)
def test_python_module_importing_hy_imports_in_a_fresh_interpreter(module: str) -> None:
    result = _fresh_import(f"import {module}")
    assert result.returncode == 0, result.stderr[-2000:]


@pytest.mark.parametrize("statement", PUBLIC_STATEMENTS)
def test_public_names_import_in_a_fresh_interpreter(statement: str) -> None:
    result = _fresh_import(statement)
    assert result.returncode == 0, result.stderr[-2000:]
