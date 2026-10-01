"""書き換えない表 Table の型の宣言(table.pyi)の失敗ケース(agora-redesign #2254)。

table.hy は Hy の module で、型の宣言(table.pyi)が無いと pyright は使い手の Table・TableDraft・table_of・draft_of を Unknown
として読み、表を欄や引数に持つ所の型が全部 Unknown に引きずられる(agora の画面の cache.hy で 300 → 434 件の赤 — 書き手に直せない)。
宣言が在れば:
- 表を作る・下書きに書く・凍らせる・引く行に「型が分からない」の赤が出ない。
- 表の答えの型の取り違え(int の size に str を足す)は赤になる。
失敗ケース: 同じ table.hy を宣言の無い局所の module として写して読むと、同じ行が Unknown の赤になる。
"""

import contextlib
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk val])
(import {source} [Table TableDraft TableWrite table-of draft-of])
(defk counted [table]
  {{:pre [(: table (get Table int))] :post [(: % int)]}}
  "見本: 表の行数。"
  (.size table))
(val base (table-of #((TableWrite "a" 1) (TableWrite "b" 2))))
(val draft (draft-of base))
(.put draft "c" 3)
(val frozen (.freeze draft))
(val first-row (.row frozen "a"))
(val all-rows (.rows frozen))
(val wrong (+ (.size frozen) "x"))
"""

#: 型が見えないと Unknown になる名(作る口・表・下書き・その答え)。
WATCHED = ('"table_of"', '"draft_of"', '"base"', '"draft"', '"frozen"', '"first_row"', '"all_rows"', '"put"', '"freeze"')


@dataclass(frozen=True)
class Run:
    """doeff-hy-check を 1 回走らせた結果(JSON の診断)。"""

    diagnostics: tuple[dict[str, object], ...]

    def errors(self) -> list[tuple[str, int, str]]:
        return [
            (str(d["rule"]), int(str(d["line"])), str(d["message"]))
            for d in self.diagnostics
            if d["severity"] == "error"
        ]


def _check(root: Path) -> Run:
    from doeff_hy.static_check import main

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
    run = _check(_probe(tmp_path, "doeff_hy.table"))
    assert not _unknown_on_watched(run), run.errors()
    # 表を作る・下書きに書く・凍らせる・引く行(7〜12 行目)には赤が 1 つも無い。
    assert not [e for e in run.errors() if 7 <= e[1] <= 12], run.errors()


@needs_pyright
def test_a_table_answer_used_as_the_wrong_type_is_red(tmp_path: Path) -> None:
    # size の答えが int と読めるので、str を足す(13 行目)と型の取り違えで赤。
    errors = _check(_probe(tmp_path, "doeff_hy.table")).errors()
    assert [e for e in errors if e[1] == 13 and e[0] == "reportOperatorIssue"], errors


@needs_pyright
def test_the_counterexample_a_table_without_the_declaration_reads_as_unknown(tmp_path: Path) -> None:
    import doeff_hy.table as table

    # 同じ table.hy を、宣言(.pyi)の無い module として検査の根の外に置いて読む(根の中の .hy は検査がその場で投影するので外に置く)
    # — 同じ行が Unknown の赤になる。
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.copy(Path(str(table.__file__)), outside / "local_table.hy")
    root = tmp_path / "root"
    root.mkdir()
    (root / "pyrightconfig.json").write_text(json.dumps({"extraPaths": [str(outside)]}), encoding="utf-8")
    run = _check(_probe(root, "local_table"))
    assert _unknown_on_watched(run), run.errors()
