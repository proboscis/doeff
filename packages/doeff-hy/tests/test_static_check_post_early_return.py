"""`:post` の型の契約 `(: % T)` と本体の途中の `(return x)` を両方持つ defk / deff の、型検査の赤の失敗ケース(agora-redesign #4254・
親 #3167)。

doeff 66d4c9428(agora-redesign #1823)から、`:post` を持つ定義の途中の `(return x)` は最後の式と同じ出口 — `_contract_result = x` →
guard → `_hy_let_% = _contract_result` → `:post` の確かめ → `return _contract_result` — に書き換わる。型の注記 `_contract_result: T` は
最後の式の代入にしか付かず、本体の最後が `while True` の形では最後の代入が到達しない所に置かれて注記がどこにも効かなかった。
途中の出口の値の型の穴(`tuple[Unknown, ...]` など)が guard の引数・`_hy_let_%`・return へのコピーへ伝わり、書き手の code の側から
直せない赤が return ごとに 3 つ増えた(`:post` の確かめを合成した位置 = `:post` の行に出る)。展開が関数の頭(`:pre` の確かめの直後)で
`_contract_result: T` を値なしで宣言し、途中と最後の代入を注記なしの要素 1 つのタプルの代入 `(_contract_result,) = (値,)` にすれば:

- strict で、`:post` の行の赤と `_hy_let_*` を文言に含む赤が 0 件(本体の最後が while True の形・最後の式に届く形・途中の return が
  複数在る形)。`_contract_result` を文言に含む赤は、値に型の穴の在る return の行に 1 つずつだけ残る — 書き手の値の穴そのもの
  (Python で `return tuple(rows)` と書いた時の「Return type ... is partially unknown」と同じ数・同じ行。pyright は宣言の在る
  変数への代入でも、代入した値の型の穴を報せる)で、書き手が値に型を付ければ消える。
- 最後の式が網羅した match の定義は赤が 0 件。素の代入 `(setv _contract_result 値)` だと、Hy が match の一時の名
  (case の前の `一時の名 = None` を含む)を `_contract_result` へ移し替え、その None が頭の宣言の T と突き合わされて書き手に
  直せない赤になった(使い手の測定 — 速さを測る module で 18 件)。タプルの代入先には移し替えが起きない。
- 型は逃げていない: 型の穴の無い定義は赤が 0 件で、途中の return で T と違う型を返すとその return の行が赤。
- 型検査のための展開と実行時の展開は同じ形(どちらも頭で 1 度だけ宣言し、代入は注記なしのタプルの代入)。
- `:post` に型の契約が無い定義は今までどおり(宣言を出さない)。

この直しの外: 値の型がまるごと Unknown(型の引数の無い list を回した要素など)の時は、pyright が宣言の在る変数をその Unknown に
絞るので、guard・`_hy_let_%`・return の赤は残る(最後の式の出口も 66d4c9428 の前から同じ形)。
"""

import ast
import contextlib
import importlib
import io
import json
import re
import shutil
from collections import Counter
from pathlib import Path

import hy
import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

# 本体の最後が while True で、途中の return だけが出口(最後の式の代入は到達しない所に置かれる)。
LOOP = """\
(require doeff-hy.macros [defk])

(defk drain [rows]
  {:pre [(: rows list)] :post [(: % (get tuple #(int ...)))]}
  (while True
    (when (not rows)
      (return (tuple rows)))
    (.pop rows)))
"""

# 途中の return と、最後の式に届く出口の両方を持つ。
REACHES_LAST = """\
(require doeff-hy.macros [defk])

(defk pick [rows]
  {:pre [(: rows list)] :post [(: % (get tuple #(int ...)))]}
  (when (not rows)
    (return (tuple rows)))
  #(1 2))
"""

# 途中の return が複数在る(繰り返しの中・while True の中)。deff も同じ展開を通る。どの return の値も型の引数の無い list から
# 作ったタプル(型の穴を持つ)。
MANY_RETURNS = """\
(require doeff-hy.macros [defk deff])

(defk split-rows [rows]
  {:pre [(: rows list)] :post [(: % (get tuple #(int ...)))]}
  (for [n (range 3)]
    (when (= (len rows) n)
      (return (tuple rows))))
  (while True
    (when (not rows)
      (return (tuple (reversed rows))))
    (.pop rows)))

(deff drain-names [names]
  {:pre [(: names list)] :post [(: % (get tuple #(str ...)))]}
  (while True
    (when (not names)
      (return (tuple names)))
    (when (= (len names) 1)
      (return (tuple (reversed names))))
    (.pop names)))
"""

# 型の穴の無い定義(引数に型の引数を書いた)— 赤は 0 件で、途中の return の型の取り違えだけが赤になる。
TYPED = """\
(require doeff-hy.macros [defk])

(defk drain-typed [rows]
  {:pre [(: rows (get list int))] :post [(: % (get tuple #(int ...)))]}
  (while True
    (when (not rows)
      (return (tuple rows)))
    (.pop rows)))
"""

# 最後の式が網羅した match(Hy は case の前に一時の名へ None を入れる)。
MATCH_LAST = """\
(require doeff-hy.macros [defk])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] InMemory [])
(defclass [(dataclass :frozen True)] OverHttp [])

(defk place-label [place]
  {:pre [(: place (| InMemory OverHttp))] :post [(: % str)]}
  (match place
    (InMemory) "memory"
    (OverHttp) "http"))
"""

# :post に写す型が無い(型が説明の文字列だけ — defk / deff は :post に (: % …) を要る)。
UNTYPED_POST = """\
(require doeff-hy.macros [defk deff])

(defk nonempty [rows]
  {:pre [(: rows (get list int))] :post [(: % "空でないタプル") (> (len %) 0)]}
  (when (not rows)
    (return (tuple [0])))
  (tuple rows))

(deff nonempty-pure [rows]
  {:pre [(: rows (get list int))] :post [(: % "空でないタプル") (> (len %) 0)]}
  (when (not rows)
    (return (tuple [0])))
  (tuple rows))
"""


def _check(root: Path, text: str) -> list[dict[str, object]]:
    from doeff_hy.static_check import main

    (root / "probe.hy").write_text(text, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--no-cache", "--strict", str(root / "probe.hy")])
    printed = out.getvalue()
    found: list[dict[str, object]] = json.loads(printed) if printed.strip() else []
    return [d for d in found if d["severity"] == "error"]


def _lines_with(text: str, fragment: str) -> list[int]:
    return [index for index, line in enumerate(text.splitlines(), 1) if fragment in line]


def _line_of(text: str, fragment: str) -> int:
    return _lines_with(text, fragment)[0]


@needs_pyright
@pytest.mark.parametrize("probe", [LOOP, REACHES_LAST, MANY_RETURNS], ids=["while-true", "reaches-last", "many-returns"])
def test_early_returns_add_no_error_of_the_expansion(tmp_path: Path, probe: str) -> None:
    errors = _check(tmp_path, probe)
    # テスト用の定義が型検査まで届いた事(書き手の値の穴 — 型の引数の無い list の引数 — の赤は書き手の行に残る)。
    assert [d for d in errors if d["rule"] == "reportMissingTypeArgument"], errors
    # 展開が :post の確かめを合成した位置(guard の引数・`_hy_let_%`・return へのコピー)に赤が無い。
    post_lines = _lines_with(probe, ":post")
    assert [d for d in errors if d["line"] in post_lines] == [], errors
    assert [d for d in errors if "_hy_let_" in str(d["message"])] == [], errors
    # `_contract_result` を文言に含む赤は、return の行にちょうど 1 つずつ — 値の型の穴(書き手の行・書き手が直せる)だけ。
    naming_result = Counter(d["line"] for d in errors if "_contract_result" in str(d["message"]))
    assert naming_result == Counter(_lines_with(probe, "(return ")), errors


@needs_pyright
def test_a_typed_definition_with_early_returns_is_clean(tmp_path: Path) -> None:
    assert _check(tmp_path, TYPED) == []


@needs_pyright
def test_a_match_as_the_last_form_is_clean(tmp_path: Path) -> None:
    # 素の代入だと Hy が match の一時の名(case の前の None)を `_contract_result` へ移し替え、
    # 「Type "None" is not assignable to declared type "str"」が match の行に出る。
    assert _check(tmp_path, MATCH_LAST) == []


@needs_pyright
def test_an_early_return_of_the_wrong_type_is_red_at_that_return(tmp_path: Path) -> None:
    # 頭の宣言が途中の出口の型を逃がしていない事(Any に逃げていれば赤にならない)。
    text = TYPED.replace("(return (tuple rows))", "(return [1])")
    errors = _check(tmp_path, text)
    assert _line_of(text, "(return [1])") in [d["line"] for d in errors], errors


def _expand(source: str) -> str:
    # doeff_hy の import が Hy の importer と macro を用意する(副作用のための import)
    importlib.import_module("doeff_hy")
    return ast.unparse(hy.compiler.hy_compile(hy.read_many(source), "__main__"))


def _both_expansions(source: str) -> dict[str, str]:
    from doeff_hy.static_view import static_view

    runtime = _expand(source)
    with static_view():
        static = _expand(source)
    return {"runtime": runtime, "static": static}


@pytest.mark.parametrize("view", ["runtime", "static"])
def test_both_expansions_declare_the_result_once_at_the_head(view: str) -> None:
    text = _both_expansions(LOOP + REACHES_LAST.replace("(require doeff-hy.macros [defk])\n", ""))[view]
    for name in ("drain", "pick"):
        body = text.split(f"def {name}(", 1)[1].split("\ndef ", 1)[0]
        lines = [line.strip() for line in body.splitlines()]
        # 値なしの宣言がちょうど 1 つ。注記つきの代入・素の代入は無い(出口はタプルの代入)。
        assert lines.count("_contract_result: 'tuple[int, ...]'") == 1, body
        assert not re.search(r"_contract_result: '[^']*' =", body), body
        assert not re.search(r"^\s*_contract_result = ", body, re.MULTILINE), body
        # 宣言は最初の代入(途中の出口)より前 — 関数の頭(:pre の確かめの直後・本体の前)。
        declared = lines.index("_contract_result: 'tuple[int, ...]'")
        first_assign = next(i for i, line in enumerate(lines) if line.startswith("_contract_result, = ("))
        first_body = next(i for i, line in enumerate(lines) if line.startswith(("while ", "if ")))
        assert declared < first_body < first_assign, body
        if view == "runtime":
            # 実行時の展開は :pre の isinstance を出す — 宣言はその後(型検査のための展開は引数の isinstance を出さない)。
            pre_check = next(i for i, line in enumerate(lines) if line.startswith("assert isinstance(rows, list)"))
            assert pre_check < declared, body


@pytest.mark.parametrize("view", ["runtime", "static"])
def test_a_post_without_a_type_contract_declares_nothing(view: str) -> None:
    text = _both_expansions(UNTYPED_POST)[view]
    assert "_contract_result:" not in text, text
    # 途中の出口と最後の式の代入(定義 2 つ × 2)は注記なしのタプルの代入。
    assert text.count("_contract_result, = (") == 4, text
