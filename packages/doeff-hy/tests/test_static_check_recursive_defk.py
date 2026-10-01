"""自分を呼び直す defk の答えの型(agora-redesign #2308・#2279 / #2291 の対)の失敗ケース。

型検査のための展開の defk は `<-` と `(! …)` が `_doeff_perform` になり本体に yield が無い普通の関数で、`do` の型
(static_types.pyi)が答えを `Expand[T, Never]` にする。前は関数の返り値に注記が無く、pyright が本体から答えの型を
推していた。自分を呼び直す defk(行ごとに判断を呼んで列を組む再帰)では、推論の途中の自分の呼びが Unknown になり、
呼び直しの答えを束ねた名と関数の返り値に strict の reportUnknown* の赤が出た(書き手に直せない赤 — #2244 で cc1-w30 が
実測)。展開が契約の `(: % T)` の T を返り値の注記に写せば:

- strict で、呼び直しの答えを名に束ねる再帰と、溜めを引数で運ぶ再帰の検体に赤が 0 件。
- 答えの型は逃げていない: 本体の中の呼び直しの答え(tuple)に数を足せば型の取り違えで赤。
- 契約の型が説明の文字列だけの `(: % "…")` の再帰は、写す型が無いので今までどおり(Unknown の赤が残る)。
- 実行時の展開は返り値に注記しない。本体に yield を直に書いた defk は生成器なので、型検査の展開でも注記しない。
"""

import ast
import contextlib
import importlib
import io
import json
import shutil
from pathlib import Path

import hy
import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

PROBE = """\
(require doeff-hy.macros [defk val <-])

(defk label-of [n]
  {:pre [(: n int)] :post [(: % str)]}
  (str n))

(defk labels-of [items]
  {:pre [(: items (get tuple #(int ...)))] :post [(: % (get tuple #(str ...)))]}
  (if (not items)
      #()
      (do
        (val head (! (label-of (get items 0))))
        (val rest (! (labels-of (cut items 1 None))))
        (+ #(head) rest))))

(defk gather [items acc]
  {:pre [(: items (get tuple #(int ...))) (: acc (get tuple #(str ...)))]
   :post [(: % (get tuple #(str ...)))]}
  (if (not items)
      acc
      (! (gather (cut items 1 None) (+ acc #((! (label-of (get items 0)))))))))

(defk total-width [items]
  {:pre [(: items (get tuple #(int ...)))] :post [(: % int)]}
  (<- labels (labels-of items))
  (<- gathered (gather items #()))
  (+ (len labels) (len gathered)))
"""

DESCRIBED = """\
(require doeff-hy.macros [defk val])

(defk depth [n]
  {:pre [(: n int)] :post [(: % "呼び直しの深さ")]}
  (if (<= n 0)
      0
      (do
        (val below (! (depth (- n 1))))
        (+ below 1))))
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


def _line_of(text: str, fragment: str) -> int:
    return next(index for index, line in enumerate(text.splitlines(), 1) if fragment in line)


@needs_pyright
def test_recursive_defk_has_no_strict_error(tmp_path: Path) -> None:
    assert _check(tmp_path, PROBE) == []


@needs_pyright
def test_recursive_answer_used_as_the_wrong_type_is_red(tmp_path: Path) -> None:
    # 本体の中の呼び直しの答え rest は tuple[str, ...] と読めるので、1 を足すと型の取り違えの赤
    # — 返り値の注記が答えを Any に逃がしていないことの検(Any なら赤にならない)。
    text = PROBE.replace("(+ #(head) rest)", "(+ #(head) (+ rest 1))")
    errors = _check(tmp_path, text)
    assert ("reportOperatorIssue", _line_of(text, "(+ rest 1)")) in [
        (d["rule"], d["line"]) for d in errors
    ], errors


@needs_pyright
def test_recursion_with_a_described_answer_keeps_the_unknown(tmp_path: Path) -> None:
    # 契約の型が説明の文字列だけなら写す型が無い — 呼び直しの答えを束ねた名は今までどおり Unknown の赤。
    errors = _check(tmp_path, DESCRIBED)
    below = _line_of(DESCRIBED, "(val below")
    assert [d for d in errors if d["line"] == below and str(d["rule"]).startswith("reportUnknown")], errors


def _expand(source: str) -> str:
    # doeff_hy の import が Hy の importer と macro を用意する(副作用のための import)
    importlib.import_module("doeff_hy")
    return ast.unparse(hy.compiler.hy_compile(hy.read_many(source), "__main__"))


YIELDING = """\
(require doeff-hy.macros [defk])

(defk raw-step [n]
  {:pre [(: n int)] :post [(: % int)]}
  (yield n)
  n)
"""


def test_only_the_static_expansion_of_a_yield_free_defk_annotates_the_return() -> None:
    from doeff_hy.static_view import static_view

    source = PROBE
    runtime = _expand(source)
    with static_view():
        static = _expand(source)
        yielding = _expand(YIELDING)
    # 型検査のための展開: 契約の (: % T) の T が返り値の注記になる(文字列の注記 — 定義の時に評価しない)
    assert "def labels_of(items: 'tuple[int, ...]') -> 'tuple[str, ...]':" in static, static
    assert "def label_of(n: 'int') -> 'str':" in static, static
    # 実行時の defk は生成器なので返り値に注記しない
    assert "def labels_of(items: 'tuple[int, ...]'):" in runtime, runtime
    assert "-> 'tuple[str, ...]'" not in runtime
    # 本体に yield を直に書いた defk は型検査の展開でも生成器 — 返り値に T を書かない
    assert "def raw_step(n: 'int'):" in yielding, yielding
