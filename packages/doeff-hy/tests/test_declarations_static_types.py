"""Failure cases for the :tags / :effects expansion of deff and the types of doeff_hy._declarations (declarations.pyi) (child of #2324).

After a definition, deff emits `setattr(name, '__doeff_tags__', doeff_hy._declarations.DefinitionTags(...))` and
`setattr(name, '__doeff_effects__', doeff_hy._declarations.effect_types(...))`. The expansion for type checking removes module-level bookkeeping,
but the bookkeeping of a deff inside a class (a method) remains in the class body. declarations.hy is a Hy module with no type declarations, so on the user side
strict reportUnknownMemberType appeared as one pair (`declarations`, `DefinitionTags`) per method (agora's
controllers/screen/tests/world.hy: 64 errors for 32 methods). -> Declare them in declarations.pyi.
"""

import ast
import contextlib
import inspect
import io
import json
import shutil
from dataclasses import dataclass, fields
from pathlib import Path

import pytest

import doeff_hy  # noqa: F401  # enable the Hy import hook
from doeff_hy import _declarations as declarations

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not available")

MODULE = """\
(require doeff-hy.macros [deff defeffect])

(defeffect Ping
  "Subject effect."
  {:answer int :tags {:context "probe" :role "intent"}})

(deff shout [text]
  {:pre [(: text str)] :post [(: % str)] :tags {:context "probe" :role "judgment"} :effects [Ping]}
  "Module-level deff."
  (.upper text))

(defclass Probe []
  "Subject class that has a deff method."
  (deff greet [self name]
    {:pre [(: self Probe) (: name str)] :post [(: % str)] :tags {:context "probe" :role "judgment"} :effects [Ping]}
    "A deff inside a class (a method)."
    (+ "hello " name))
  (deff plain [self]
    {:pre [(: self Probe)] :post [(: % int)] :tags {:context "probe" :role "judgment" :spells "json"}}
    "A method with spells."
    1))
"""

#: Messages of the errors that previously appeared on the deff lines (none could be fixed by the author).
FORMER_ERRORS: tuple[str, ...] = (
    'Type of "_declarations" is unknown',
    'Type of "DefinitionTags" is unknown',
    'Type of "effect_types" is unknown',
)


@dataclass(frozen=True)
class Run:
    """Result of running doeff-hy-check once (JSON diagnostics)."""

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
def test_deff_tags_and_effects_have_no_unreadable_red(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _check(tmp_path).errors()
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if any(m in e[2] for m in FORMER_ERRORS)], errors
    assert not [e for e in errors if "unknown" in e[2].lower() and "declarations" in e[2]], errors


def test_the_stub_matches_declarations_hy() -> None:
    # The names declared by declarations.pyi exist in declarations.hy, and the function parameter names and order and the DefinitionTags fields are the same as the implementation
    # (the declarations alone do not get ahead).
    stub = ast.parse(Path(declarations.__file__).with_suffix(".pyi").read_text(encoding="utf-8"))
    constants = [
        node.target.id
        for node in stub.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]
    functions = [node for node in stub.body if isinstance(node, ast.FunctionDef)]
    classes = [node.name for node in stub.body if isinstance(node, ast.ClassDef)]
    declared = constants + [f.name for f in functions] + classes
    assert [name for name in declared if not hasattr(declarations, name)] == []
    for function in functions:
        assert [a.arg for a in function.args.args] == list(
            inspect.signature(getattr(declarations, function.name)).parameters
        ), function.name
    assert classes == ["DefinitionTags"]
    assert [f.name for f in fields(declarations.DefinitionTags)] == ["context", "role", "spells", "reads"]
