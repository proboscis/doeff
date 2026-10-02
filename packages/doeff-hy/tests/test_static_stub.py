"""Hy の module の型の宣言(.pyi)を型検査のための展開から作る道具(doeff_hy.static_stub)の失敗ケース(agora-redesign #2826)。

手で書いた .pyi は実装と食い違い、doeff の定義を defn → defk に変えるたびに使い手(agora)の型の門が Unknown の赤で止まった
(2026-10-02 に 4 回)。道具は展開の木から宣言を作り、一致の検は「作り直した物 == commit された物」。

- 定義の形ごと(defk・deff・defhandler・defrecord・defenum・定数・型の別名・import)に、使い手が読める宣言が出る。
- 型を出せない所は Incomplete にして名を返す(黙って Unknown にしない)。
- 実装を変えると、commit された .pyi が古いと名指される(一致の検の失敗ケース)。手で書いた .pyi は照らさない。
"""

from pathlib import Path

import pytest

from doeff_hy.static_stub import MARK, generated, stale_in, stub_of

MODULE = """\
(require doeff-hy.macros [defk deff defhandler val <-])
(require doeff-hy.record [defrecord defenum])
(import doeff [EffectBase Program])
(import dataclasses [dataclass])

(val GREETING "hi")
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
