"""Check that the top-level names of doeff_core_effects (Ask, Get, reader, etc.) are visible to type checkers as public names.

In a py.typed package, names merely imported in `__init__.py` are not considered public (`__all__` or `import X as X` is required).
For someone who copied `from doeff_core_effects import Ask, Get, Put, Tell` from the README Quick Start, their editor and pyright
reported "Ask" is not exported from module (reportPrivateImportUsage).

This repo does not place `__all__` (doeff-linter DOEFF021 - `doeff/__init__.py` also uses `X as X`), so top-level names are bound with `X as X`.
HTTP names (`_LAZY_EXPORTS` - a Hy module that pulls in httpx) are loaded for the first time via `__getattr__`, so they cannot be bound with `X as X`
(doing so would make the import fail in a core-only environment). This also guards that a star import does not go through `__getattr__` and load the Hy module.

Failure cases: red if a name is left without `X as X`, or an HTTP name is bound at the top level.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import doeff_core_effects
from doeff_hy._static_check import python_without_bytecode

PACKAGE_DIR = Path(doeff_core_effects.__file__).resolve().parent

#: Names that the examples in the README and the getting-started chapter (docs/01-getting-started.md) take from the package top.
README_NAMES = ("Ask", "Get", "Put", "Tell")


def _head_imports() -> list[ast.alias]:
    """Names bound at the top of `__init__.py` as `from doeff_core_effects.<sub> import name` (bound at runtime, names that do not load Hy)."""
    tree = ast.parse((PACKAGE_DIR / "__init__.py").read_text(encoding="utf-8"))
    return [
        alias
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("doeff_core_effects.")
        for alias in node.names
    ]


def _head_names() -> list[str]:
    return sorted(alias.name for alias in _head_imports())


def test_the_head_binds_the_readme_names() -> None:
    assert set(README_NAMES) <= set(_head_names())


def test_every_name_the_head_binds_is_a_redundant_alias_reexport() -> None:
    # `import X as X` is the re-export marker for py.typed packages. Names without the marker are read by type checkers as "not public".
    plain = sorted(alias.name for alias in _head_imports() if alias.asname != alias.name)
    assert plain == [], f"make these X as X: {plain}"


def test_the_head_does_not_bind_the_lazy_names() -> None:
    # If bound at the top, `import doeff_core_effects` fails in a core-only environment (no Hy module and no httpx).
    assert set(_head_names()).isdisjoint(doeff_core_effects._LAZY_EXPORTS)


def test_every_head_name_is_bound_in_the_module() -> None:
    unbound = [name for name in _head_names() if name not in vars(doeff_core_effects)]
    assert unbound == [], unbound


def test_star_import_does_not_load_the_hy_modules() -> None:
    code = (
        "import sys\n"
        "namespace = {}\n"
        "exec('from doeff_core_effects import *', namespace)\n"
        "loaded = sorted(m for m in ('httpx', 'doeff_core_effects._http_handlers', 'doeff_core_effects._http_effects') if m in sys.modules)\n"
        "assert 'Ask' in namespace and 'reader' in namespace and 'HttpRequest' not in namespace, sorted(namespace)\n"
        "assert not loaded, loaded\n"
    )
    done = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr


def _pyright_errors(tmp_path: Path, source: str, mode: str) -> list[str]:
    """Check one file in tmp_path against the doeff_core_effects installed in this environment's python (same as users get, a py.typed package)."""
    config = tmp_path / "pyrightconfig.json"
    config.write_text(json.dumps({"typeCheckingMode": mode}), encoding="utf-8")
    sample = tmp_path / "head_imports.py"
    sample.write_text(source, encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "pyright", "--project", str(config),
         "--pythonpath", str(python_without_bytecode(tmp_path, sys.executable)), "--outputjson", str(sample)],
        cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False,
    )
    report = json.loads(done.stdout)
    return [f"{item['range']['start']['line'] + 1}: {item['message']}" for item in report["generalDiagnostics"]
            if item["severity"] == "error"]


def test_pyright_accepts_the_readme_imports(tmp_path: Path) -> None:
    source = (
        f"from doeff_core_effects import {', '.join(README_NAMES)}\n"
        "from doeff_core_effects import reader, state, writer\n"
        "\n"
        f"_ = ({', '.join(README_NAMES)}, reader, state, writer)\n"
    )
    assert _pyright_errors(tmp_path, source, "standard") == []


def test_pyright_accepts_every_name_the_head_binds(tmp_path: Path) -> None:
    source = "from doeff_core_effects import (\n" + "".join(f"    {name},\n" for name in _head_names()) + ")\n"
    assert _pyright_errors(tmp_path, source, "standard") == []
