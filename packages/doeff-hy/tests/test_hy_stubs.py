"""Failure cases for the stubs of the hy parts that doeff-hy ships (src/hy-stubs).

hy has no type declarations. The Hy expansion compiles keyword literals and quoted forms into `hy.models.Keyword(...)` and the like,
and doeff-hy-check puts `import hy.models` into the modules that read them, so previously every new .hy file always produced
`Stub file not found for "hy.models"` (and "hy") as a strict red (the writer could not fix it — 758 files in agora's base).

- A small .hy using hy names and models produces neither the reportMissingTypeStubs for hy / hy.models nor the "type unknown" red.
- The names the stub declares exist in the runtime hy (declarations do not run ahead), and the stub is partial (undeclared lower modules come from hy's source).
- A .hy that writes defmacro produces neither the "type unknown" nor the "not an attribute of hy" red for the expansion's `hy.macros.macro('name')(...)` (hy.macros's
  stub macros.pyi; 6 cases in controllers/shared/intent/patrols.hy).
"""

import ast
import contextlib
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import hy
import hy.macros
import hy.models
import pytest

import doeff_hy  # noqa: F401  # enables the Hy import hook

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not available")

STUBS = Path(doeff_hy.__file__).parent.parent / "hy-stubs"

MODULE = """\
(require doeff-hy.macros [val])
(import hy.models [Keyword Symbol])

(val KEYS [:a :b])
(val FORM (quote (f "x" 1 2.5)))
(val HEAD (str (get FORM 0)))
(val NAME (+ (. (Keyword "k") name) (hy.mangle "a-b")))
(val SYMBOL (Symbol "s"))
"""


MACRO_MODULE = """\
(defmacro defpoint [#^ hy.models.Symbol name #^ hy.models.Object #* fields]
  "Places one value from a name and fields."
  `(setv ~name [~@fields]))
"""


@dataclass(frozen=True)
class Run:
    """The result of running doeff-hy-check once (JSON diagnostics)."""

    diagnostics: tuple[dict[str, object], ...]

    def errors(self) -> list[tuple[str, int, str]]:
        return [
            (str(d["rule"]), int(str(d["line"])), str(d["message"]))
            for d in self.diagnostics
            if d["severity"] == "error"
        ]


def _check(root: Path) -> Run:
    from doeff_hy._static_check import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")])
    text = out.getvalue()
    return Run(tuple(json.loads(text)) if text.strip() else ())


@needs_pyright
def test_a_new_hy_file_has_no_missing_hy_stub(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _check(tmp_path).errors()
    assert not [e for e in errors if e[0] == "reportMissingTypeStubs"], errors
    assert not [e for e in errors if e[0].startswith("reportUnknown")], errors


@needs_pyright
def test_a_defmacro_file_has_no_unknown_hy_macro(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MACRO_MODULE, encoding="utf-8")
    errors = _check(tmp_path).errors()
    assert not [e for e in errors if '"macro"' in e[2]], errors
    assert not [e for e in errors if e[0].startswith("reportUnknown")], errors


def _names_of(node: ast.stmt) -> tuple[str, ...]:
    """The name declared by one top-level statement of the stub (for a re-export, the `as` name)."""
    match node:
        case ast.ClassDef(name=name) | ast.FunctionDef(name=name) | ast.AnnAssign(target=ast.Name(id=name)):
            return (name,)
        case ast.ImportFrom(names=aliases):
            return tuple(a.asname for a in aliases if a.asname is not None)
        case _:
            return ()


def _declared(stub: Path) -> list[str]:
    """The public names declared at the top level of the stub module (names starting with _ are types only inside the stub)."""
    tree = ast.parse(stub.read_text(encoding="utf-8"))
    return [
        n for node in tree.body for n in _names_of(node) if not n.startswith("_") or n.startswith("__")
    ]


def test_the_stub_names_exist_in_hy() -> None:
    assert [n for n in _declared(STUBS / "__init__.pyi") if not hasattr(hy, n)] == []
    assert [n for n in _declared(STUBS / "models.pyi") if not hasattr(hy.models, n)] == []
    assert [n for n in _declared(STUBS / "macros.pyi") if not hasattr(hy.macros, n)] == []
    # Lower modules without declarations (hy.compiler and the like) are read from hy's source.
    assert (STUBS / "py.typed").read_text(encoding="utf-8").strip() == "partial"
