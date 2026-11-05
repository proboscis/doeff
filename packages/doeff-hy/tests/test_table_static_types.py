"""Failure cases for the type declaration (table.pyi) of the immutable Table.

table.hy is a Hy module, and without a type declaration (table.pyi) pyright reads the user's Table, TableDraft, table_of and draft_of as Unknown,
so every type that holds a table in a field or argument is dragged to Unknown (300 -> 434 reds in agora's screen cache.hy — the author cannot fix them).
With the declaration:
- Lines that build a table, write to a draft, freeze it, or look up a row produce no "type unknown" red.
- A type mismatch in a table answer (adding a str to an int size) is a red.
Failure case: copying the same table.hy as a local module without a declaration and reading it turns the same lines into Unknown reds.
"""

import contextlib
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not installed")

MODULE = """\
(require doeff-hy.macros [defk val])
(import {source} [Table TableDraft TableWrite table-of draft-of])
(defk counted [table]
  {{:pre [(: table (get Table int))] :post [(: % int)]}}
  "Example: the number of rows in a table."
  (.size table))
(val base (table-of #((TableWrite "a" 1) (TableWrite "b" 2))))
(val draft (draft-of base))
(.put draft "c" 3)
(val frozen (.freeze draft))
(val first-row (.row frozen "a"))
(val all-rows (.rows frozen))
(val wrong (+ (.size frozen) "x"))
"""

#: Names that become Unknown when the type is not visible (the constructors, the table, the draft, and their answers).
WATCHED = ('"table_of"', '"draft_of"', '"base"', '"draft"', '"frozen"', '"first_row"', '"all_rows"', '"put"', '"freeze"')


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


def _probe(root: Path, source: str) -> Path:
    (root / "probe.hy").write_text(MODULE.format(source=source), encoding="utf-8")
    return root


def _unknown_on_watched(run: Run) -> list[tuple[str, int, str]]:
    return [e for e in run.errors() if e[0].startswith("reportUnknown") and any(n in e[2] for n in WATCHED)]


@needs_pyright
def test_using_a_table_has_no_unknown(tmp_path: Path) -> None:
    run = _check(_probe(tmp_path, "doeff_hy._table"))
    assert not _unknown_on_watched(run), run.errors()
    # Lines that build a table, write to a draft, freeze it, or look up a row (lines 7-12) have no reds at all.
    assert not [e for e in run.errors() if 7 <= e[1] <= 12], run.errors()


@needs_pyright
def test_a_table_answer_used_as_the_wrong_type_is_red(tmp_path: Path) -> None:
    # The size answer reads as int, so adding a str (line 13) is a red for a type mismatch.
    errors = _check(_probe(tmp_path, "doeff_hy._table")).errors()
    assert [e for e in errors if e[1] == 13 and e[0] == "reportOperatorIssue"], errors


@needs_pyright
def test_the_counterexample_a_table_without_the_declaration_reads_as_unknown(tmp_path: Path) -> None:
    import doeff_hy._table as table

    # Place the same table.hy outside the check root as a module without a declaration (.pyi) and read it (a .hy inside the root is projected on the spot by the check, so put it outside)
    # — the same lines become Unknown reds.
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.copy(Path(str(table.__file__)), outside / "local_table.hy")
    root = tmp_path / "root"
    root.mkdir()
    (root / "pyrightconfig.json").write_text(json.dumps({"extraPaths": [str(outside)]}), encoding="utf-8")
    run = _check(_probe(root, "local_table"))
    assert _unknown_on_watched(run), run.errors()
