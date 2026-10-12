"""doeff-linter が読む節の操作の一覧(data/clause_ops.json)が、表 1 つ(doeff_hy/clause_ops.hy の CLAUSE-OPS)から書き出した物と
食い違わない事の検(card ki-d3ef2a50cc04)。

節の操作の名(finish など)と終わり方の定義元は doeff-hy の表 CLAUSE-OPS 1 か所(card ki-b4c015d8ba1f)。ところが doeff-linter(Rust)の
business_fakes.rs には、操作の名を直に match する所が残っていて、表に行を足しても linter が追いつかず、定義が 2 か所に戻った。

- Rust は Hy を評価できないので、linter は表から書き出した file を読む(file は生成物 — scripts/export_clause_ops.py --write)。
- 表に行を足す・直すと、書き出す中身が変わる。file をそのままにすると赤になる(書き直す 1 手だけで linter の判じが追従する —
  追従は Rust の検 business_fakes の `clause_shapes_follow_every_row_of_the_table`・`a_row_added_to_the_table_is_followed_by_the_judgement`)。
- file に表を介さず名を足す・消す・直すと赤になる(linter の側へ名を持ち込む道の 1 つ。business_fakes.rs へ名を直に書く道は、
  Rust の検 `clause_operation_names_are_not_written_outside_the_table` が赤にする)。
"""

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import doeff_hy  # 拡張を登録する(clause_ops.hy を読むため)
import pytest
from doeff_hy import clause_ops
from doeff_hy.clause_ops import ClauseOp, Ending
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
    """file は表の書き出しと 1 字も違わない — 表を直して書き直し忘れても、file に名を直に足しても赤。"""
    assert _LINTER_TABLE.read_text(encoding="utf-8") == clause_ops.linter_table_json(), (
        f"{_LINTER_TABLE.name} が表 CLAUSE-OPS と食い違う — `{_REGENERATE}` で書き直す"
    )


def test_every_row_of_the_table_is_written_with_the_fields_the_linter_reads() -> None:
    """行は表の順に、名・終わり方・引数の数だけを持つ(linter が読む欄)。"""
    assert _rows(clause_ops.linter_table_json()) == [
        {"name": op.name, "ending": op.ending.value, "arity": op.arity}
        for op in clause_ops.CLAUSE_OPS
    ]


def test_a_row_added_to_the_table_is_written_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """表に行を足すと書き出しに載り、file とは食い違う(書き直せば足した行が linter に届く)。"""
    monkeypatch.setattr(clause_ops, "CLAUSE_OPS", (*clause_ops.CLAUSE_OPS, _THROW))
    written = clause_ops.linter_table_json()
    assert {"name": "throw", "ending": "resumes", "arity": 1} in _rows(written)
    assert written != _LINTER_TABLE.read_text(encoding="utf-8")


def test_a_row_dropped_from_the_table_leaves_the_written_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """表から行を消すと書き出しから消える(file に残った行は食い違いとして赤になる)。"""
    monkeypatch.setattr(
        clause_ops, "CLAUSE_OPS", tuple(op for op in clause_ops.CLAUSE_OPS if op.name != "finish")
    )
    assert "finish" not in {row["name"] for row in _rows(clause_ops.linter_table_json())}


def test_every_ending_in_the_table_is_one_the_linter_knows() -> None:
    """linter の読み(src/project/clause_ops.rs の Ending)は 4 つ — 表に新しい終わり方を足したら、linter の判じも足す。"""
    assert {ending.value for ending in Ending} == {"resumes", "finishes", "forwards", "raises"}


def test_export_script_checks_the_file_against_the_table() -> None:
    """script は既定で照らすだけ(食い違いなら 1)。"""
    result = subprocess.run(
        [sys.executable, "-B", "-I", str(_EXPORT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
