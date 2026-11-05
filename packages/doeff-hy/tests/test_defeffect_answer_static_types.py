"""Failure cases showing that the :answer type of defeffect survives in the type-checking expansion.

defeffect places the answer type in `__doeff_answer__`. The type-checking expansion (doeff-hy-check) removes the bookkeeping setattr (`__doeff_...__`), so
when the answer type was placed with setattr, the :answer expression vanished from type checking, and names imported only for the answer type became a reportUnusedImport red
(removing them breaks things at runtime, so the writer could not fix it). The expansion now places it in the class body as `__doeff_answer__: ClassVar[object]`:

- Imported names that appear only in :answer are treated as used imports (no red). Imports that do not appear in :answer stay red (the check is not loosened).
- Writing a bare generic (`dict`) or `Callable | None` in :answer adds no reds from evaluating them as types in a value position (reportMissingTypeArgument,
  reportOperatorIssue).
- At runtime `__doeff_answer__` is the :answer expression itself, and the ClassVar annotation does not become a dataclass field.
"""

import ast
import contextlib
import dataclasses
import inspect
import io
import json
import shutil
import typing
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import doeff_hy  # noqa: F401  # enables the Hy import hook
import hy
import pytest

from doeff import EffectBase

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not available")

# The answer types Decimal, Fraction, and Callable appear only in :answer. PurePath does not appear in :answer (an import that is truly unused).
MODULE = """\
(require doeff-hy.macros [defeffect])
(import collections.abc [Callable])
(import decimal [Decimal])
(import fractions [Fraction])
(import pathlib [PurePath])

(defeffect ReadAmount
  "Sample read (the answer type is a union of imported names)."
  {:fields [(: key str)]
   :answer (| Decimal Fraction None)
   :tags {:context "probe" :role "intent"}})

(defeffect ReadTable
  "Sample read (answers with a bare generic and Callable | None)."
  {:fields []
   :answer (| dict Callable None)
   :tags {:context "probe" :role "intent"}})
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


@pytest.fixture
def probe(tmp_path: Path) -> Path:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    return tmp_path


def _line_of(marker: str) -> int:
    """The line number (from 1) of the line containing the marker in the sample."""
    return next(n for n, line in enumerate(MODULE.splitlines(), start=1) if marker in line)


@needs_pyright
def test_answer_only_imports_are_used_and_unused_ones_stay_red(probe: Path) -> None:
    errors = _check(probe).errors()
    unused = [e for e in errors if e[0] == "reportUnusedImport"]
    # Names that appear only in :answer are used imports (removing the fix brings back 3 reds: Decimal, Fraction, Callable).
    assert not [
        e for e in unused if any(f'"{n}"' in e[2] for n in ("Decimal", "Fraction", "Callable"))
    ], unused
    # An import that does not appear in :answer stays red (the expansion does not treat all imports as used).
    assert [e for e in unused if e[1] == _line_of("[PurePath]") and '"PurePath"' in e[2]], unused


@needs_pyright
def test_answer_does_not_add_type_form_errors(probe: Path) -> None:
    # A bare dict or Callable | None in :answer produces no red from being evaluated as a type in a value position. The defeffect line has no reds.
    errors = _check(probe).errors()
    lines = range(_line_of("(defeffect ReadAmount"), len(MODULE.splitlines()) + 1)
    assert not [e for e in errors if e[1] in lines], errors


def test_projection_keeps_the_answer_in_the_class_body() -> None:
    # In the type-checking expansion, __doeff_answer__ is an annotated assignment with ClassVar[object] in the class body, and it reads the names in :answer.
    # It is not placed in the bookkeeping setattr (which type checking removes).
    from doeff_hy._static_view import static_view

    with static_view():
        module = hy.compiler.hy_compile(hy.read_many(MODULE), "__main__", import_stdlib=False)
    assert isinstance(module, ast.Module)
    source = ast.unparse(module)
    assert (
        "__doeff_answer__: _doeff_ClassVar[object] = _doeff_cast(object, (Decimal, Fraction, None))"
        in source
    )
    assert "setattr(ReadAmount, '__doeff_answer__'" not in source


def test_runtime_answer_is_the_declared_type_and_not_a_field() -> None:
    namespace: dict[str, object] = {}
    hy.eval(hy.models.Expression([hy.models.Symbol("do"), *hy.read_many(MODULE)]), namespace)
    read_amount = namespace["ReadAmount"]
    read_table = namespace["ReadTable"]
    assert isinstance(read_amount, type)
    assert issubclass(read_amount, EffectBase)
    assert dataclasses.is_dataclass(read_amount)
    assert isinstance(read_table, type)
    assert vars(read_amount)["__doeff_answer__"] == Decimal | Fraction | None
    assert vars(read_table)["__doeff_answer__"] == dict | Callable | None
    assert typing.get_origin(read_amount.__annotations__["__doeff_answer__"]) is typing.ClassVar
    assert tuple(f.name for f in dataclasses.fields(read_amount)) == ("key",)
    assert tuple(inspect.signature(read_amount).parameters) == ("key",)
    assert vars(read_amount(key="a")) == {"key": "a"}
