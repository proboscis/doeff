"""定義の宣言 :effects にだけ出る効果の名を、型検査が「使われていない import」と数えない(agora-redesign #3871 の後)の失敗ケース。

handler の節が効果を下請けの関数(別の module の defk)を通して出す時、その効果の型の名は handler の頭の :effects の宣言にだけ
現れる。型検査のための展開(static_check.without_bookkeeping)は定義の記帳 `setattr(名, '__doeff_effects__', …)` を丸ごと外して
いたので、宣言にだけ出る名は読まれない import になり、strict の reportUnusedImport の赤が出た(agora の
controllers/messaging/protocol/intake_router.hy・intake.hy の AppendEvent — 2026-10-07 08:00 の日次)。import を消すと実行の時に
:effects の宣言が名を解けずに落ちるので、書き手は直せない。

- 宣言にだけ出る名を import した handler の検体に、reportUnusedImport の赤が無い。
- 記帳の :effects の文は、doeff_hy.declarations を呼ばない形 `setattr(名, '__doeff_effects__', (A, B))` に縮めて残す(ほかの記帳と
  doeff_hy.declarations の import は今までどおり外す)。
"""

import ast
import contextlib
import io
import json
import shutil
from pathlib import Path

import pytest
from doeff_hy.static_check import without_bookkeeping

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

EFFECTS = """\
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] Tick [(get EffectBase int)]
  #^ int step)

(defclass [(dataclass :frozen True)] Pair [(get EffectBase int)]
  #^ int left
  #^ int right)
"""

HELPERS = """\
(require doeff-hy.macros [defk <-])
(import probe_effects [Pair])

(defk paired [step]
  {:pre [(: step int)] :post [(: % int)] :tags {:context "probe" :role "protocol"}}
  "Pair を出して答えを返すため(handler の節が呼ぶ下請け)。"
  (<- n int (Pair step step))
  n)
"""

PROBE = """\
(require doeff-hy.macros [defhandler <-])
(import probe_effects [Tick Pair])
(import probe_helpers [paired])

(defhandler via-helper {:effects [Pair] :tags {:context "probe" :role "protocol"}}
  ;; Pair は下請け paired が出す — この module では宣言 :effects にだけ現れる。
  (Tick [step]
    (<- n int (paired step))
    (resume n)))
"""


def _check(root: Path) -> list[dict[str, object]]:
    from doeff_hy.static_check import main

    (root / "probe_effects.hy").write_text(EFFECTS, encoding="utf-8")
    (root / "probe_helpers.hy").write_text(HELPERS, encoding="utf-8")
    (root / "probe.hy").write_text(PROBE, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--no-cache", "--strict", str(root / "probe.hy")])
    printed = out.getvalue()
    found: list[dict[str, object]] = json.loads(printed) if printed.strip() else []
    return [d for d in found if d["severity"] == "error"]


@needs_pyright
def test_a_name_only_in_the_effects_declaration_is_not_an_unused_import(tmp_path: Path) -> None:
    errors = _check(tmp_path)
    assert [e for e in errors if e["rule"] == "reportUnusedImport"] == [], errors


def test_the_effects_bookkeeping_keeps_its_names_without_the_declarations_module() -> None:
    tree = ast.parse(
        "from m import A, B\n"
        "import doeff_hy.declarations\n"
        "setattr(h, '__doeff_effects__', doeff_hy.declarations.effect_types('defhandler h', tuple([A, B])))\n"
        "setattr(h, '__doeff_tags__', doeff_hy.declarations.DefinitionTags(context='c', role='protocol'))\n"
        "setattr(h, '__doeff_needs__', None)\n"
    )
    projected = ast.unparse(without_bookkeeping(tree)).splitlines()
    assert projected == ["from m import A, B", "setattr(h, '__doeff_effects__', (A, B))"], projected


def test_an_undeclared_effects_bookkeeping_is_dropped() -> None:
    # :effects を書かない定義の記帳(None)は今までどおり外す。
    tree = ast.parse("setattr(h, '__doeff_effects__', None)\n")
    assert ast.unparse(without_bookkeeping(tree)) == ""
