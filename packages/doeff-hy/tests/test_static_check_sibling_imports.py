"""Even when doeff-hy-check (doeff_hy/_static_check.py) checks only one file, it must expand the .hy next to the importing file's dir and
have pyright resolve it.

When a script is run with `hy scripts/x.hy`, scripts/ goes to the head of the search path, so the script reads a sibling module by a top-level name
(`(import e2e_wire [...])`). pyright also resolves this form by "searching parent dirs from the importing file's dir up to the root". The earlier dependency
expansion searched only under the import roots (the repo root and extraPaths), so checking a single file did not expand the sibling .hy, and
`Import "b" could not be resolved` and the resulting Unknown errors (`Type of "helper" is unknown`, `Argument type is unknown`) were
reported. Checking a whole dir did not show them because the sibling .hy was also a file to check -- the same file got different answers depending on how it was checked.
"""

import contextlib
import io
import json
import shutil
from pathlib import Path

import pytest

from doeff_hy._static_check import main

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not available")

# The sibling module (the side the script reads by a top-level name).
SIBLING = """(require doeff-hy.macros [defk])

(defk helper [n]
  {:pre [(: n int)] :post [(: % int)]}
  "To double it."
  (* n 2))
"""

# A script that imports a name from the sibling module and calls the function twice (the two forms of #2898 -- importing a new name and adding a call).
SCRIPT = """(require doeff-hy.macros [defk <-])
(import b [helper])

(defk twice [n]
  {:pre [(: n int)] :post [(: % int)]}
  "To double it twice."
  (<- once (helper n))
  (<- again (helper once))
  again)
"""

# A sibling .hy with the same name as a standard library module (pyright resolves the standard library first -- it looks at the sibling only when that fails).
SHADOW = """(require doeff-hy.macros [defk])

(defk dumps [n]
  {:pre [(: n int)] :post [(: % int)]}
  "A function of the sibling module with the same name as a standard library module (must not be read)."
  n)
"""

USES_STDLIB = """(require doeff-hy.macros [defk])
(import json)

(defk shown [n]
  {:pre [(: n int)] :post [(: % str)]}
  "To turn a number into a JSON string."
  (json.dumps n))
"""


def _checked(root: Path, *paths: str) -> list[dict[str, object]]:
    """Check paths (files or dirs) under root with strict and read the --json answer."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--strict", "--json", "--no-cache", *(str(root / p) for p in paths)])
    return json.loads(out.getvalue())


def _errors_of(diagnostics: list[dict[str, object]], path: str) -> list[tuple[object, object, object]]:
    """List the errors of one file as (line, rule, message) -- to compare two answers obtained in different ways of checking."""
    return sorted(
        (d["line"], d["rule"], d["message"])
        for d in diagnostics
        if d["path"] == path and d["severity"] == "error"
    )


def _repo(root: Path, files: dict[str, str]) -> Path:
    """Build the root of a subject holding the strict pyright config and files (path relative to the root -> content)."""
    (root / "pyproject.toml").write_text('[tool.pyright]\ntypeCheckingMode = "strict"\n', encoding="utf-8")
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    return root


def test_search_dirs_put_the_import_roots_first_then_the_file_dir_up_to_the_root(tmp_path: Path) -> None:
    from doeff_hy._static_check import search_dirs

    root = tmp_path / "repo"
    extra = root / "clients" / "hy"
    source = root / "scripts" / "deep" / "x.hy"
    assert search_dirs(root, [root, extra], source) == [root, extra, root / "scripts" / "deep", root / "scripts"]
    # A file directly under the root searches only the root (the same dir is not searched twice); it does not go above the root.
    assert search_dirs(root, [root], root / "x.hy") == [root]


@needs_pyright
def test_a_single_file_resolves_its_sibling_module_like_the_whole_dir(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"scripts/b.hy": SIBLING, "scripts/a.hy": SCRIPT})
    alone = _errors_of(_checked(root, "scripts/a.hy"), "scripts/a.hy")
    whole = _errors_of(_checked(root, "scripts"), "scripts/a.hy")
    assert alone == whole == [], (alone, whole)


@needs_pyright
def test_a_sibling_named_like_the_standard_library_does_not_shadow_it(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"scripts/json.hy": SHADOW, "scripts/a.hy": USES_STDLIB})
    alone = _errors_of(_checked(root, "scripts/a.hy"), "scripts/a.hy")
    whole = _errors_of(_checked(root, "scripts"), "scripts/a.hy")
    assert alone == whole == [], (alone, whole)
