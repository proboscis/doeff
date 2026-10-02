"""Hy の module の型の宣言(.pyi)を型検査のための展開から作る道具(doeff_hy.static_stub)の失敗ケース(agora-redesign #2826)。

手で書いた .pyi は実装と食い違い、doeff の定義を defn → defk に変えるたびに使い手(agora)の型の門が Unknown の赤で止まった
(2026-10-02 に 4 回)。道具は展開の木から宣言を作り、一致の検は「作り直した物 == commit された物」。

- 定義の形ごと(defk・deff・defhandler・defrecord・defenum・定数・型の別名・import)に、使い手が読める宣言が出る。
- 型を出せない所は Incomplete にして名を返す(黙って Unknown にしない)。
- 実装を変えると、commit された .pyi が古いと名指される(一致の検の失敗ケース)。手で書いた .pyi は照らさない。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from doeff_hy.static_stub import MARK, UsedModule, generated, stale_in, stub_of, users_probe

MODULE = """\
(require doeff-hy.macros [defk deff defhandler val <-])
(require doeff-hy.record [defrecord defenum])
(import doeff [EffectBase Program])
(import dataclasses [dataclass])
(import datetime [datetime])
(import os)

(val GREETING "hi")
(val HERE (os.path.dirname (os.path.abspath __file__)))
(val MAX-BYTES (* 32 1024 1024))
(val Stamp (| datetime None))
(val LIMITS #(1 2 3))
(val NAMES (+ #("a") #("b")))
(val SQL (+ "SELECT 1" " FROM t"))
(val Answer (| int str))
(val LIMIT-OF {"a" 1 "b" 2})
(val UNKNOWABLE (sorted LIMITS))

(defenum Color RED BLUE)
(val PRIMARY Color.RED)

(defrecord Point
  "平面の点。"
  (#^ int x)
  (#^ int y))

(defclass [(dataclass :frozen True)] Ping [(get EffectBase int)]
  "答えは int。"
  (#^ str target)
  (setv #^ int tries 3))

(deff double [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "probe" :role "judgment"}}
  "2 倍にするため。"
  (* n 2))

(defk ping-twice [target]
  {:pre [(: target str)] :post [(: % int)] :tags {:context "probe" :role "program"}}
  "2 回 ping して答えを足すため。"
  (<- first int (Ping target))
  (<- second int (Ping target))
  (+ first second))

(defk passed-through [body]
  {:pre [(: body Program)] :post [(: % "本文の答え")] :tags {:context "probe" :role "program"}}
  "本文の答えをそのまま返すため(答えの型は本文による)。"
  (<- answer body)
  answer)

(defhandler counting [#^ (get list int) seen]
  (Ping [target tries]
    (resume (+ tries (len seen)))))

(defhandler answering-one
  (Ping [target tries]
    (resume 1)))

(defclass _Sentinel [])
(val SENTINEL (_Sentinel))
"""


def _module(tmp_path: Path, text: str = MODULE) -> Path:
    """検の module を tmp の根の下の package に置く(根 = tmp_path)。"""
    source = tmp_path / "probe_pkg" / "probe_mod.hy"
    source.parent.mkdir(parents=True, exist_ok=True)
    (source.parent / "__init__.py").write_text("", encoding="utf-8")
    source.write_text(text, encoding="utf-8")
    return source


def _lines(tmp_path: Path) -> list[str]:
    source = _module(tmp_path)
    return stub_of(tmp_path, [tmp_path], source).text.splitlines()


@pytest.mark.parametrize(
    "line",
    [
        "GREETING: str",
        "LIMITS: tuple[int, ...]",
        "NAMES: tuple[str, ...]",
        "SQL: str",
        "Answer: TypeAlias = int | str",
        "LIMIT_OF: dict[str, int]",
        "PRIMARY: Color",
        "class Point:",
        "class Ping(EffectBase[int]):",
        "    tries: int = 3",
        "def double(n: int) -> int:",
        "def ping_twice(target: str) -> _Program[int, object]:",
        "def counting(seen: list[int]) -> _Handler:",
        "answering_one: _Handler",
        "HERE: str",
        "MAX_BYTES: int",
        "Stamp: TypeAlias = datetime | None",
        "SENTINEL: _Sentinel",
        "def passed_through(body: Program) -> _Program[Incomplete, object]:",
        "from doeff import EffectBase as EffectBase",
    ],
)
def test_each_kind_of_definition_gets_a_declaration(tmp_path: Path, line: str) -> None:
    assert line in _lines(tmp_path)


def test_the_stub_starts_with_the_mark_and_has_no_bodies(tmp_path: Path) -> None:
    lines = _lines(tmp_path)
    assert lines[0].startswith(MARK)
    assert not [line for line in lines if "_doeff_perform" in line or "yield" in line]


def test_the_macro_helpers_the_expansion_imports_are_not_reexported(tmp_path: Path) -> None:
    # defk の展開は doeff_hy.macros の補助(_install_guard_globals・_guard_performed など)を import する。型の面ではないので
    # 公開し直さない — 公開し直すと、.pyi が macro の module に依存する形になり、品質検査の依存の契約にも当たった(#2842)。
    assert not [line for line in _lines(tmp_path) if line.startswith("from doeff_hy.macros import _")]


def test_what_cannot_be_typed_is_named_not_left_unknown(tmp_path: Path) -> None:
    # 説明の文字列の :post と、sorted の答えは型を出せない — Incomplete にして名を返す。
    source = _module(tmp_path)
    made = stub_of(tmp_path, [tmp_path], source)
    assert made.incomplete == ("UNKNOWABLE", "passed_through の答え")
    assert "UNKNOWABLE: Incomplete" in made.text.splitlines()
    assert "from _typeshed import Incomplete" in made.text.splitlines()


def test_a_current_stub_is_not_stale(tmp_path: Path) -> None:
    source = _module(tmp_path)
    source.with_suffix(".pyi").write_text(stub_of(tmp_path, [tmp_path], source).text, encoding="utf-8")
    assert stale_in(tmp_path) == ()


def test_a_changed_field_makes_the_committed_stub_stale(tmp_path: Path) -> None:
    # 失敗ケース: 実装の欄を足すと、commit された .pyi が古いと名指される。
    source = _module(tmp_path)
    source.with_suffix(".pyi").write_text(stub_of(tmp_path, [tmp_path], source).text, encoding="utf-8")
    source.write_text(MODULE.replace("  (#^ int y))", "  (#^ int y)\n  (#^ int z))"), encoding="utf-8")
    assert [(s.source.name, s.reason) for s in stale_in(tmp_path)] == [("probe_mod.hy", "作り直した物と違う")]


def test_a_hand_written_stub_is_not_checked(tmp_path: Path) -> None:
    # 手で書いた .pyi(印が無い)は道具の物と見なさない — 一致の検にも --write にも入らない。
    source = _module(tmp_path)
    source.with_suffix(".pyi").write_text("GREETING: str\n", encoding="utf-8")
    assert not generated(source.with_suffix(".pyi"))
    assert stale_in(tmp_path) == ()


EFFECTS = """\
(require doeff-hy.macros [defeffect])

(defeffect Pong
  "答えは int。"
  {:fields [(: target str)]
   :answer int
   :tags {:context "probe" :role "foundation"}})

(defeffect MaybePong
  "答えは int か None。"
  {:fields [(: target str)]
   :answer (| int None)
   :tags {:context "probe" :role "foundation"}})

(defeffect NoneTypePong
  "答えの式が型の形でない(実行時の値)— 基底は答えを載せない素の形のまま。"
  {:fields [(: target str)]
   :answer (type None)
   :tags {:context "probe" :role "foundation"}})
"""

USER = """\
(require doeff-hy.macros [defk <-])
(import probe_pkg.probe_mod [Pong])

(defk use-pong [target]
  {:pre [(: target str)] :post [(: % int)] :tags {:context "probe" :role "program"}}
  "Pong の答えを使うため。"
  (<- n (Pong target))
  n)
"""


@pytest.mark.parametrize(
    "line",
    [
        "from doeff import EffectBase as _doeff_effect_base",
        "from dataclasses import dataclass as _doeff_dataclass",
        "@_doeff_dataclass(frozen=True)",
        "class Pong(_doeff_effect_base[int]):",
        "class MaybePong(_doeff_effect_base[int | None]):",
        "class NoneTypePong(_doeff_effect_base):",
    ],
)
def test_a_defeffect_keeps_its_base_with_the_answer_and_its_dataclass(tmp_path: Path, line: str) -> None:
    # 失敗ケース(#2886): 道具は macro の補助の import(_doeff_ で始まる名)を外すのに、defeffect の基底と飾りはその名を
    # 読むので、.pyi に定義の無い基底(使い手には Unknown)が残り、dataclass の飾りは消えていた。基底は答えの型も持たなかった。
    source = _module(tmp_path, EFFECTS)
    assert line in stub_of(tmp_path, [tmp_path], source).text.splitlines()


def test_a_user_reads_the_answer_of_a_generated_effect(tmp_path: Path) -> None:
    # 失敗ケース(#2886): 使い手の `(<- n (Pong target))` の n は、生成された .pyi の基底が Unknown だったので Unknown に読まれ、
    # strict の型検査が「型が分からない」の赤を 7 件出した。答えの型 int が基底に載れば、n は int に読める。
    source = _module(tmp_path, EFFECTS)
    source.with_suffix(".pyi").write_text(stub_of(tmp_path, [tmp_path], source).text, encoding="utf-8")
    user = source.with_name("user_mod.hy")
    user.write_text(USER, encoding="utf-8")
    command = [sys.executable, "-m", "doeff_hy.static_check", "--root", str(tmp_path), "--json", "--strict", "--no-cache", str(user)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=240, check=False)
    found = json.loads(done.stdout) if done.stdout.strip() else []
    assert [f"{d['line']}: {d['rule']}: {d['message']}" for d in found if d["severity"] == "error"] == []


def test_the_users_probe_binds_each_name_on_its_own_line() -> None:
    # 名を 1 つの tuple に束ねると、全く分からない名が「型の一部が分からない」に紛れる — 名ごとに別の名へ束ねる。
    used = (UsedModule("a.b", ("Thing", "make-thing")), UsedModule("c", ("LIMIT",)))
    assert users_probe("pkg", used).splitlines() == [
        "(import pkg.a.b [Thing make-thing])",
        "(import pkg.c [LIMIT])",
        "",
        "(setv used-0 Thing)",
        "(setv used-1 make-thing)",
        "(setv used-2 LIMIT)",
    ]
