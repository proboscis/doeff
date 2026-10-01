"""契約 `(: x T)` に、要素の型つきの総称型を含む型の別名を書ける事の失敗ケース(agora-redesign #2512・#1790)。

defk の :pre の型の契約は、実行の時は isinstance で確かめ(要素の型つきの総称型は外側の型へ写す — #1790)、型検査の展開でも同じ
isinstance を出していた。型の別名(名)はそのまま isinstance へ渡るので、要素の型を書いた別名(例 `Body = Mapping[str, Body] | …`)を
型検査の時だけ書く形(doeff_hy/json_value.py と同じ)にしても、型検査で『isinstance の第 2 引数は型でない』の赤になり、別名を素の和
(`Mapping | tuple | …`)のままにするしか無かった(使い手の strict に『一部不明』の赤が残る)。
→ 型検査の展開では :pre の型の契約の isinstance を出さない(引数の型は契約から書く注記が運ぶ)。実行の時の確かめは変えない。
1 本目は型検査の赤が無い事、2 本目は実行の時に契約が今までどおり違う値を断る事を確かめる。
"""

import contextlib
import importlib
import io
import json
import shutil
import sys
from pathlib import Path

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
import pytest

from doeff import run

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk val])
(import collections.abc [Mapping])
(import typing [TYPE_CHECKING TypeAlias])

;; 型検査の時は要素の型つきの再帰の別名・実行の時は isinstance に渡せる和(doeff_hy/json_value.py と同じ形)。
(if TYPE_CHECKING
    (setv #^ TypeAlias Body "Mapping[str, Body] | tuple[Body, ...] | str | int | None")
    (setv Body (| Mapping tuple str int None)))

(defk text-of [body key]
  {:pre [(: body Body) (: key str)] :post [(: % (| str None))] :tags {:context "probe" :role "judgment"}}
  "中身が object で欄 key が文字列ならその値を読むため(別名の要素の型で読める)。"
  (val value (if (isinstance body Mapping) (.get body key) None))
  (if (isinstance value str) value None))
"""


def _errors(root: Path) -> list[tuple[str, int, str]]:
    from doeff_hy.static_check import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(
            ["--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe_alias.hy")]
        )
    text = out.getvalue()
    diagnostics = json.loads(text) if text.strip() else []
    return [
        (str(d["rule"]), int(str(d["line"])), str(d["message"]))
        for d in diagnostics
        if d["severity"] == "error"
    ]


@needs_pyright
def test_a_type_alias_with_element_types_can_be_a_pre_contract(tmp_path: Path) -> None:
    (tmp_path / "probe_alias.hy").write_text(MODULE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "isinstance" in e[2]], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors


def test_the_pre_contract_still_refuses_a_wrong_value_at_run_time(tmp_path: Path) -> None:
    (tmp_path / "probe_alias_run.hy").write_text(MODULE, encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        probe = importlib.import_module("probe_alias_run")
    finally:
        sys.path.remove(str(tmp_path))
    assert run(probe.text_of({"a": "x"}, "a")) == "x"
    assert run(probe.text_of((1, 2), "a")) is None
    with pytest.raises(AssertionError, match="pre-condition type error"):
        run(probe.text_of(object(), "a"))
