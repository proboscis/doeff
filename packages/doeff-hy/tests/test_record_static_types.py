"""Failure cases for the :check expansion of defrecord and the types of doeff_hy.record (record.pyi).

A defrecord with :check emits `(import doeff_hy._declarations doeff_hy.record doeff_hy._record_check)` and names `doeff_hy.record.DoExpr`,
`doeff_hy.record.EffectBase`, and `doeff_hy._record_check.require_check` (require_check used to live in `doeff_hy.record` -
a macro module shows only macros, ADR-DOE-API-001 R14). Previously 5 strict reds appeared on the user-side defrecord line:

- record.hy is a Hy module with no type declarations, and the 4 names it points to (record, DoExpr, EffectBase, require_check) are Unknown.
    -> declare them in record.pyi.
- The type-check expansion removes the bookkeeping (setattr of `__doeff_tags__`), so declarations is no longer read, but it sits in the same single import statement as record,
    so the whole statement stayed and became reportUnusedImport. -> imports of the bookkeeping module are removed one name at a time (static_check).
"""

import ast
import contextlib
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import hy
import pytest

import doeff
import doeff_hy  # noqa: F401  # enable the Hy import hook
from doeff_hy import record
from doeff_hy._static_check import without_bookkeeping

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not available")

MODULE = """\
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])

(defrecord Answer
  "The answer of the specimen."
  {:tags {:context "probe" :role "type"}
   :check [(isinstance value key)]}
  (#^ type key)
  (#^ object value))

(defrecord Span
  "The range of the specimen (a :check without :tags)."
  {:check [(<= start end)]}
  (#^ int start)
  (#^ int end))
"""

#: The wording of the reds that used to appear on the defrecord line (none can be fixed by the writer).
FORMER_ERRORS: tuple[str, ...] = (
    '"doeff_hy._declarations" is not accessed',
    'Type of "record" is unknown',
    'Type of "DoExpr" is unknown',
    'Type of "EffectBase" is unknown',
    'Type of "require_check" is unknown',
)


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
def test_a_checked_defrecord_has_no_unreadable_red(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _check(tmp_path).errors()
    assert not [e for e in errors if any(m in e[2] for m in FORMER_ERRORS)], errors


def _expand(source: str) -> str:
    return ast.unparse(hy.compiler.hy_compile(hy.read_many(source), "__main__"))


def test_only_the_runtime_expansion_refuses_a_program_verdict() -> None:
    # The judgment whether a :check answer is a Program (`isinstance(v, (DoExpr, EffectBase))`) exists only in the runtime expansion.
    # If it is in the type-check expansion, pyright 1.1.414 reads this tuple, depending on a file checked earlier in the same process, as
    # `tuple[UnionType, type[EffectBase[_Answer]]]` and raised reportArgumentType for class_or_tuple (agora-controllers'
    # daily run - only when the whole repo was measured with `--threads 2`, and one of two same-shaped cases in the same file). The writer cannot fix it.
    from doeff_hy._static_view import static_view

    runtime = _expand(MODULE)
    with static_view():
        static = _expand(MODULE)
    assert "doeff_hy.record.DoExpr" in runtime, runtime
    assert "doeff_hy.record.DoExpr" not in static, static
    assert "doeff_hy.record.EffectBase" not in static, static
    # require_check, which rejects a false answer, stays in both expansions, and is read from the inner module rather than the macro module (doeff_hy.record)
    assert "doeff_hy._record_check.require_check" in static, static
    assert "doeff_hy._record_check.require_check" in runtime, runtime
    assert "doeff_hy.record.require_check" not in runtime + static


def test_an_unread_bookkeeping_name_leaves_a_shared_import_alone() -> None:
    # Of the bookkeeping module names lined up in one statement, remove only the unread declarations and keep the read record.
    tree = ast.parse(
        "import doeff_hy._declarations, doeff_hy.record\n"
        "isinstance(v, (doeff_hy.record.DoExpr, doeff_hy.record.EffectBase))\n"
        "setattr(A, '__doeff_tags__', doeff_hy._declarations.DefinitionTags(context='c', role='type'))\n"
    )
    projected = ast.unparse(without_bookkeeping(tree))
    assert projected.splitlines()[0] == "import doeff_hy.record", projected


def test_a_bookkeeping_import_with_no_reader_is_dropped() -> None:
    tree = ast.parse(
        "import doeff_hy._declarations, doeff_hy.record\n"
        "setattr(A, '__doeff_tags__', doeff_hy._declarations.DefinitionTags(context='c', role='type'))\n"
    )
    assert ast.unparse(without_bookkeeping(tree)) == ""


def test_the_stub_matches_record_hy() -> None:
    # The names record.pyi declares live in record.hy (declarations alone do not go ahead), and it declares no functions - record.hy shows nothing but macros
    # as functions (require_check is in the inner doeff_hy._record_check).
    stub = ast.parse(Path(record.__file__).with_suffix(".pyi").read_text(encoding="utf-8"))
    declared = [
        alias.asname or alias.name
        for node in stub.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ] + [node.name for node in stub.body if isinstance(node, ast.FunctionDef)]
    assert [name for name in declared if not hasattr(record, name)] == []
    assert [node for node in stub.body if isinstance(node, ast.FunctionDef)] == []
    assert not hasattr(record, "require_check")
    assert record.DoExpr is doeff.DoExpr
    assert record.EffectBase is doeff.EffectBase
