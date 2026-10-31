"""宣言を持つ effect の `<-` の束ねた名の型と、宣言を開かない中継 `Perform` の型。

ADR-DOE-CORE-EFFECTS-003: 実行時の `<-` は :absent の答えを Absent に・:failure の答えを Raise に変えて呼び手のスコープで出すので、
束ねた名には成功と :value の答えだけが来る。型検査の展開(`_doeff_perform`)も同じ型を返す:

- `(<- x (| Hit Empty) (Read …))` — Read の :answer が `Hit | Empty | Missing | Broken` で :absent [Missing]・:failure [Broken] なら、
  x の型は `Hit | Empty`(注記が答えの全部だった頃は、Missing と Broken が注記に入らず赤になった)。
- 注記が束ねた名の型より狭ければ赤のまま(検は緩めていない)。
- 中継 `(<- a (Perform ask))` は宣言を開かないので、答えは宣言の全部(effect の union も受ける)。
- `on-raise` の展開が import する `_doeff_on_raise` は型が全部分かる(DoExpr の総称の node の型引数が Unknown だと、呼び手の strict が
  「部分的に不明」の赤になった)。
- effect の基底の答えは宣言の全部のまま — handler の `typed_resume` は 3 つとも値で答える。
"""

import json
import subprocess
import sys
from pathlib import Path

from doeff_hy._static_stub import stub_of

EFFECTS = """\
(require doeff-hy.macros [defeffect])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] Hit [] (#^ int n))
(defclass [(dataclass :frozen True)] Empty [] (#^ str why))
(defclass [(dataclass :frozen True)] Missing [] (#^ str why))
(defclass [(dataclass :frozen True)] Broken [] (#^ str detail))

(defeffect Read
  "失敗と不在を宣言した読み。"
  {:fields [(: key str)]
   :answer (| Hit Empty Missing Broken)
   :absent [Missing]
   :failure [Broken]
   :value [Empty]
   :tags {:context "probe" :role "foundation"}})

(defeffect Plain
  "宣言の無い読み。"
  {:fields [(: key str)]
   :answer (| Hit Broken)
   :tags {:context "probe" :role "foundation"}})

(defeffect Only
  "失敗しか宣言しない読み(成功の型は Hit)。"
  {:fields [(: key str)]
   :answer (| Hit Broken)
   :failure [Broken]
   :tags {:context "probe" :role "foundation"}})
"""

USER = """\
(require doeff-hy.macros [defk defhandler on-raise <-])
(import doeff [Perform Program])
(import probe_pkg.probe_mod [Read Plain Only Hit Empty Missing Broken])

(defk bound-without-failure-and-absent [key]
  {:pre [(: key str)] :post [(: % int)] :tags {:context "probe" :role "program"}}
  (<- got (| Hit Empty) (Read key))
  (if (isinstance got Hit) got.n 0))

(defk bound-of-a-failure-only-declaration [key]
  {:pre [(: key str)] :post [(: % int)] :tags {:context "probe" :role "program"}}
  (<- got Hit (Only key))
  got.n)

(defk undeclared-keeps-everything [key]
  {:pre [(: key str)] :post [(: % int)] :tags {:context "probe" :role "program"}}
  (<- got (| Hit Broken) (Plain key))
  (if (isinstance got Hit) got.n 0))

(defk relay-keeps-the-declared-answer [key]
  {:pre [(: key str)] :post [(: % int)] :tags {:context "probe" :role "program"}}
  (<- got (| Hit Empty Missing Broken) (Perform (Read key)))
  (if (isinstance got Hit) got.n 0))

(defk relay-takes-an-effect-union [ask]
  {:pre [(: ask (| Read Plain))] :post [(: % int)] :tags {:context "probe" :role "program"}}
  (<- got (| Hit Empty Missing Broken) (Perform ask))
  (if (isinstance got Hit) got.n 0))

(defhandler relay-through
  "宣言を開かずに出し直す中継の節 — 答えは宣言の全部で、typed_resume に入る。"
  {:tags {:context "probe" :role "foundation"}}
  (Read [key]
    (<- answer (Perform effect))
    (resume answer))
  (Plain [key]
    (<- answer (Perform effect))
    (resume answer)))

(defk closes-the-failure [body]
  {:pre [(: body Program)] :post [(: % "本文の答え")] :tags {:context "probe" :role "program"}}
  (<- got (on-raise body (Broken d) (len d)))
  got)

(defk bound-narrower-than-the-bound-answer [key]
  {:pre [(: key str)] :post [(: % int)] :tags {:context "probe" :role "program"}}
  (<- got Hit (Read key))
  got.n)

(defk relay-narrower-than-the-declared-answer [key]
  {:pre [(: key str)] :post [(: % int)] :tags {:context "probe" :role "program"}}
  (<- got (| Hit Empty) (Perform (Read key)))
  (if (isinstance got Hit) got.n 0))
"""


def _write(tmp_path: Path) -> Path:
    source = tmp_path / "probe_pkg" / "probe_mod.hy"
    source.parent.mkdir(parents=True, exist_ok=True)
    (source.parent / "__init__.py").write_text("", encoding="utf-8")
    source.write_text(EFFECTS, encoding="utf-8")
    return source


def test_the_stub_of_a_declared_effect_carries_the_bound_answer(tmp_path: Path) -> None:
    lines = stub_of(tmp_path, [tmp_path], _write(tmp_path)).text.splitlines()
    # 基底の答えは宣言の全部のまま(handler の typed_resume は 3 つとも値で答える)。束ねた名の型は method が持つ。
    assert "class Read(_doeff_effect_base[Hit | Empty | Missing | Broken]):" in lines
    assert "    def __iter__(self) -> _doeff_BoundRun[Hit | Empty]:" in lines
    assert "    def __iter__(self) -> _doeff_BoundRun[Hit]:" in lines
    # 答えの分け方を宣言しない effect は束ねた名の型を変えない(`__iter__` は宣言した 2 つの effect だけ)。
    assert len([line for line in lines if "def __iter__" in line]) == 2


def test_a_bind_reads_the_bound_answer_and_a_relay_reads_the_declared_one(tmp_path: Path) -> None:
    source = _write(tmp_path)
    source.with_suffix(".pyi").write_text(stub_of(tmp_path, [tmp_path], source).text, encoding="utf-8")
    user = source.with_name("user_mod.hy")
    user.write_text(USER, encoding="utf-8")
    command = [sys.executable, "-m", "doeff_hy._static_check", "--root", str(tmp_path), "--json", "--strict", "--no-cache", str(user)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=240, check=False)
    found = json.loads(done.stdout) if done.stdout.strip() else []
    errors = [(d["line"], d["rule"]) for d in found if d["severity"] == "error"]
    lines = USER.splitlines()
    narrower = [n for n, line in enumerate(lines, start=1) if "(<- got Hit (Read key))" in line or "(<- got (| Hit Empty) (Perform (Read key)))" in line]
    # 赤は「注記が狭すぎる」2 か所だけ — 束ねた名の型を満たす注記・宣言を持たない effect・中継(effect の union も)は赤にならない。
    assert errors == [(n, "reportAssignmentType") for n in narrower], (errors, found)
