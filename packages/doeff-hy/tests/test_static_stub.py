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
(val STOPPED-CODE -15)
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
        "STOPPED_CODE: int",
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
(require doeff-hy.macros [defeffect val])

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

(import typing [TypeVar])
(import doeff [Program])
(val T (TypeVar "T"))

(defeffect Wrapped
  "答えは包んだ program の答え(型の引数 T)か None。"
  {:fields [(: program (get Program #(T object)))]
   :answer (| T None)
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
        # 答えに module の TypeVar を含む effect(sql_effects の SqlTransaction の形 — #2925)。
        "class Wrapped(_doeff_effect_base[T | None]):",
        "    program: Program[T, object]",
    ],
)
def test_a_defeffect_keeps_its_base_with_the_answer_and_its_dataclass(tmp_path: Path, line: str) -> None:
    # 失敗ケース(#2886): 道具は macro の補助の import(_doeff_ で始まる名)を外すのに、defeffect の基底と飾りはその名を
    # 読むので、.pyi に定義の無い基底(使い手には Unknown)が残り、dataclass の飾りは消えていた。基底は答えの型も持たなかった。
    source = _module(tmp_path, EFFECTS)
    assert line in stub_of(tmp_path, [tmp_path], source).text.splitlines()


def test_a_defeffect_stub_carries_no_macro_bookkeeping_field(tmp_path: Path) -> None:
    # 失敗ケース(#2886): `__doeff_answer__: _doeff_ClassVar[object]` を写すと、手の .pyi の欄の照らし
    # (doeff-core-effects の test_hy_module_stubs — dataclass の欄の並び)が別名の ClassVar を見抜けずに欄と数え、
    # 置き換えた effect の module が 4 つ赤になった。答えの型は基底が持つので、記帳の名は .pyi に要らない。
    source = _module(tmp_path, EFFECTS)
    text = stub_of(tmp_path, [tmp_path], source).text
    assert "__doeff_answer__" not in text
    assert "_doeff_ClassVar" not in text


def test_a_dotted_module_import_no_declaration_reads_is_not_copied(tmp_path: Path) -> None:
    # 失敗ケース(#2886): defrecord の展開が引く `import doeff_hy.record` を .pyi に写すと、宣言はどれも読まないのに
    # .pyi が doeff_hy.record に依存する形になり、品質検査の module の依存の契約(dependency-not-allowed)に当たった
    # (meter_effects の手の .pyi を道具の出力に置き換えた時)。点つきの import は使い手に名を公開しないので写さない。
    # 頭の辞書つきの defrecord の展開は `(import doeff_hy.declarations doeff_hy.record)` を出す。
    checked = """\
(require doeff-hy.record [defrecord])

(defrecord Amount
  "負でない量。"
  {:tags {:context "probe" :role "type"}
   :check [(>= value 0)]}
  (#^ int value))
"""
    lines = stub_of(tmp_path, [tmp_path], _module(tmp_path, checked)).text.splitlines()
    assert "class Amount:" in lines
    assert [line for line in lines if line.startswith("import doeff_hy.")] == []


def test_a_module_import_no_declaration_reads_is_not_copied(tmp_path: Path) -> None:
    # 失敗ケース(#2925): `(import os)` を `import os as os` と写すと、宣言はどれも os を読まないのに .pyi が os に依存する形になり、
    # 品質検査の「純粋な層から IO の API へ依存」(pure-io-import)に当たった(process_effects の手の .pyi を置き換えた時)。
    # module の import は使い手に名を公開する意味が無い(使い手は `from m import os` と書かない)ので、宣言が読まなければ写さない。
    assert [line for line in _lines(tmp_path) if line.startswith("import os")] == []


def test_a_field_annotated_then_given_its_default_keeps_the_default(tmp_path: Path) -> None:
    # 失敗ケース(#2974): 欄を「#^ float timeout」と「(setv timeout 30.0)」の 2 文で書くと、道具は .pyi にも 2 文のまま写し
    # (`timeout: float` と `timeout = 30.0`)、pyright は dataclass の作り手を注記の文の値から組むので既定値の無い欄と読んだ —
    # 欄を省いた呼びが「timeout が無い」の赤(agora の 17 本目・ClickHouseDatabase)。実行時は既定値 30.0 の欄。
    # L1377 で手の process_effects.pyi(`timeout: float | None = None`)を置き換えた時に RunProcess など 23 の欄が同じ形に後退した。
    split = """\
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True :kw-only True)] Database []
  "置き場の宛先。"
  #^ str host
  #^ float timeout
  (setv timeout 30.0)
  #^ (| str None) user
  (setv user None))
"""
    lines = stub_of(tmp_path, [tmp_path], _module(tmp_path, split)).text.splitlines()
    assert "    host: str" in lines
    assert "    timeout: float = 30.0" in lines
    assert "    user: str | None = None" in lines
    assert [line for line in lines if line.strip().startswith(("timeout =", "user ="))] == []


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


STORE = """\
(import pathlib [Path])

(defclass Store []
  "置き場(__init__ で欄を置く class — #2972 の WalStore の形)。"
  (defn #^ None __init__ [self #^ int start #^ str label]
    (setv #^ Path self.where (Path label) self.start start #^ int self.seq start
          self.loose (dict) self._hidden 0))

  (defn #^ int next-seq [self]
    "次の番号(欄を読む method)。"
    (+ self.seq 1)))
"""

LOG_USER = """\
(require doeff-hy.macros [defk <-])
(import pathlib [Path])
(import typing [Protocol])
(import probe_pkg.probe_mod [Store])

(defclass Log [Protocol]
  "置き場の形(欄だけ — #2972 の ByteLog の形)。"
  (#^ int seq)
  (#^ Path where))

(defk seq-of [log]
  {:pre [(: log Log)] :post [(: % int)] :tags {:context "probe" :role "judgment"}}
  "置き場の形の欄を読むため。"
  log.seq)

(defk seq-of-store [store]
  {:pre [(: store Store)] :post [(: % int)] :tags {:context "probe" :role "judgment"}}
  "Store を置き場の形として渡すため(欄が宣言に無いと形を満たさない)。"
  (<- n (seq-of store))
  n)
"""


@pytest.mark.parametrize(
    "line",
    [
        # 注記つきの置き方は注記を写す。
        "    where: Path",
        "    seq: int",
        # 引数をそのまま置く置き方は、その引数の注記を写す。
        "    start: int",
        # 型を読めない置き方は Incomplete(名を返す)。
        "    loose: Incomplete",
    ],
)
def test_the_fields_an_init_places_get_declarations(tmp_path: Path, line: str) -> None:
    # 失敗ケース(#2972): 道具は __init__ の `(setv self.x …)` を読まず、method だけを宣言した。WalStore の .pyi に欄 seq・
    # snapshot・log・max_log_bytes が無く、それらを求める ByteLog を満たさないので、使い手の正しい渡し方に型の赤が出た。
    assert line in stub_of(tmp_path, [tmp_path], _module(tmp_path, STORE)).text.splitlines()


def test_an_untyped_init_field_is_named_and_a_private_one_is_not_declared(tmp_path: Path) -> None:
    made = stub_of(tmp_path, [tmp_path], _module(tmp_path, STORE))
    assert "Store.loose" in made.incomplete
    assert [line for line in made.text.splitlines() if "_hidden" in line] == []


def test_a_class_whose_init_places_the_fields_satisfies_a_protocol_of_fields(tmp_path: Path) -> None:
    # 失敗ケース(#2972): 欄の宣言が無いと `(seq-of store)` が「Store は Log の欄 seq・where を持たない」の赤になる。
    source = _module(tmp_path, STORE)
    source.with_suffix(".pyi").write_text(stub_of(tmp_path, [tmp_path], source).text, encoding="utf-8")
    user = source.with_name("log_user.hy")
    user.write_text(LOG_USER, encoding="utf-8")
    command = [sys.executable, "-m", "doeff_hy.static_check", "--root", str(tmp_path), "--json", "--strict", "--no-cache", str(user)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=240, check=False)
    found = json.loads(done.stdout) if done.stdout.strip() else []
    assert [f"{d['line']}: {d['rule']}: {d['message']}" for d in found if d["severity"] == "error"] == []
