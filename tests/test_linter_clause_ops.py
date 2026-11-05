"""Check that the clause-operation list read by doeff-linter (data/clause_ops.json) does not diverge from what is exported from the single table
(CLAUSE-OPS in doeff_hy/_clause_ops.hy).

The names of clause operations (finish, etc.) and their endings are defined in exactly one place: the doeff-hy table CLAUSE-OPS. However,
business_fakes.rs in doeff-linter (Rust) still matched operation names directly, so adding a row to the table left the linter behind and the definition went back to two places.

- Rust cannot evaluate Hy, so the linter reads a file exported from the table (the file is generated -- scripts/export_clause_ops.py --write).
- Adding or changing a row in the table changes the exported content. Leaving the file as is turns this red (regenerating it is the one step needed for the linter's judgement to follow --
  the follow-up is checked by the Rust tests in business_fakes, `clause_shapes_follow_every_row_of_the_table` and `a_row_added_to_the_table_is_followed_by_the_judgement`).
- Adding, removing, or changing a name in the file without going through the table turns this red (one way of bringing names into the linter side; writing names directly into business_fakes.rs is
  turned red by the Rust test `clause_operation_names_are_not_written_outside_the_table`).
"""

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import doeff_hy  # registers the extension (needed to read clause_ops.hy)
import pytest
from doeff_hy import _clause_ops as clause_ops
from doeff_hy._clause_ops import ClauseOp, Ending
from hy.models import Object

_ROOT = Path(__file__).resolve().parent.parent
_LINTER_TABLE = _ROOT / "packages" / "doeff-linter" / "data" / "clause_ops.json"
_EXPORT = _ROOT / "scripts" / "export_clause_ops.py"
_REGENERATE = "uv run python scripts/export_clause_ops.py --write"


def _rows(text: str) -> list[dict[str, object]]:
    return json.loads(text)["ops"]


def _identity(args: list[Object], recur: Callable[[Object], Object]) -> Object:
    return recur(args[0])


_THROW = ClauseOp(
    name="throw",
    ending=Ending.RESUMES,
    arity=1,
    returns=False,
    tail=None,
    nodes=("TransferThrow",),
    static_nodes=("TransferThrow",),
    rewrite=_identity,
)


def test_linter_table_is_the_table_written_out() -> None:
    """The file does not differ by a single character from the table export -- red whether the table was fixed without regenerating or a name was added to the file directly."""
    assert _LINTER_TABLE.read_text(encoding="utf-8") == clause_ops.linter_table_json(), (
        f"{_LINTER_TABLE.name} diverges from the table CLAUSE-OPS -- regenerate it with `{_REGENERATE}`"
    )


def test_every_row_of_the_table_is_written_with_the_fields_the_linter_reads() -> None:
    """Each row carries only name, ending, and argument count, in table order (the fields the linter reads)."""
    assert _rows(clause_ops.linter_table_json()) == [
        {"name": op.name, "ending": op.ending.value, "arity": op.arity}
        for op in clause_ops.CLAUSE_OPS
    ]


def test_a_row_added_to_the_table_is_written_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adding a row to the table shows up in the export and differs from the file (regenerating makes the added row reach the linter)."""
    monkeypatch.setattr(clause_ops, "CLAUSE_OPS", (*clause_ops.CLAUSE_OPS, _THROW))
    written = clause_ops.linter_table_json()
    assert {"name": "throw", "ending": "resumes", "arity": 1} in _rows(written)
    assert written != _LINTER_TABLE.read_text(encoding="utf-8")


def test_a_row_dropped_from_the_table_leaves_the_written_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removing a row from the table removes it from the export (a row left in the file turns red as a divergence)."""
    monkeypatch.setattr(
        clause_ops, "CLAUSE_OPS", tuple(op for op in clause_ops.CLAUSE_OPS if op.name != "finish")
    )
    assert "finish" not in {row["name"] for row in _rows(clause_ops.linter_table_json())}


def test_every_ending_in_the_table_is_one_the_linter_knows() -> None:
    """The linter reads 4 endings (Ending in src/project/clause_ops.rs) -- when a new ending is added to the table, add it to the linter's judgement too."""
    assert {ending.value for ending in Ending} == {"resumes", "finishes", "forwards", "raises"}


def test_export_script_checks_the_file_against_the_table() -> None:
    """The script only compares by default (exit 1 on divergence)."""
    result = subprocess.run(
        [sys.executable, "-B", "-I", str(_EXPORT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
