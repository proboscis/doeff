"""for/do を使う module の defk の型(agora-redesign #2321・親 #2318)の失敗ケース。

for/do の展開は `_doeff_do`・`_doeff_traverse_Traverse`・`_doeff_traverse_Skip` を名指し、書き手がその名を
`(import doeff [do :as _doeff-do])` などで import する(for/do の docstring)。型検査のための展開では doeff-hy-check が
module の頭に static_types の `do` を `_doeff_do` として置くので、同じ名に 2 つの宣言が並ぶ。前は 2 つの型が違い
(core の `do` に本体に yield の無い関数の overload が無かった)、pyright は後に来る書き手の import の型で defk の投影
(`<-` が `_doeff_perform` になり yield の無い関数)を読んで、同じ module の全ての defk の定義が赤になり、defk を呼んだ
答えが Unknown になった(agora の book_rows.hy で 47 件)。加えて、件ごとの関数の引数(`(<- x (From …))` の x)は
注記の無い def の引数になり、strict の「引数の型が分からない」が出ていた。doeff_traverse には py.typed が無く、
Traverse の答えの Collection の `valid_values` も Unknown だった。直した後は:

- strict で、for/do を使う defk と、それを呼ぶ defk の検体に赤が 0 件。
- 答えの型は逃げていない: 件の x(str)に数を足す・for/do の答えの値(int)に文字列を足すと型の取り違えの赤。
- 実行時の展開は今までどおり(件の引数をそのまま受け、型検査の補助を名指さない)で、走らせた答えも同じ。
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
(require doeff-hy.macros [defk val <- for/do])
(import doeff [do :as _doeff-do])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse [Skip :as _doeff_traverse_Skip])

(defk width-of [text]
  {:pre [(: text str)] :post [(: % int)]}
  (len text))

(defk widths [texts]
  {:pre [(: texts (get tuple #(str ...)))] :post [(: % (get tuple #(int ...)))]}
  (<- got
      (for/do
        (<- text (From texts :label "width"))
        (When (> (len text) 0))
        (<- width (width-of text))
        width))
  (val errors got.errors)
  (when errors
    (raise (get errors 0)))
  (tuple got.valid-values))

(defk total-width [texts]
  {:pre [(: texts (get tuple #(str ...)))] :post [(: % int)]}
  (<- each (widths texts))
  (<- one (width-of "a"))
  (+ (sum each) one))
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
def test_for_do_module_has_no_strict_error(tmp_path: Path) -> None:
    assert _check(tmp_path, PROBE) == []


@needs_pyright
def test_item_used_as_the_wrong_type_is_red(tmp_path: Path) -> None:
    # 件の text は items(tuple[str, ...])の要素の型 str と読める — 数を足すと型の取り違えの赤
    # (件の引数が object / Any に逃げていないことの検)。
    text = PROBE.replace("(When (> (len text) 0))", "(When (> (+ text 1) 0))")
    errors = _check(tmp_path, text)
    assert ("reportOperatorIssue", _line_of(text, "(+ text 1)")) in [
        (d["rule"], d["line"]) for d in errors
    ], errors


@needs_pyright
def test_for_do_answer_used_as_the_wrong_type_is_red(tmp_path: Path) -> None:
    # for/do の答えの有効な値は本体の答え(width-of の int)と読める — 文字列を足すと赤。
    text = PROBE.replace("(tuple got.valid-values))", '(tuple (+ (get got.valid-values 0) "x")))')
    errors = _check(tmp_path, text)
    assert ("reportOperatorIssue", _line_of(text, '"x"')) in [(d["rule"], d["line"]) for d in errors], errors


@needs_pyright
def test_defk_answer_in_a_for_do_module_is_read(tmp_path: Path) -> None:
    # for/do の module でも、defk を呼んだ答え(widths の tuple[int, ...])が読める — 文字列を足すと赤
    # (前は答えが Unknown で、足しても赤にならなかった)。
    text = PROBE.replace("(+ (sum each) one)", '(+ each "x")')
    errors = _check(tmp_path, text)
    assert ("reportOperatorIssue", _line_of(text, '"x"')) in [(d["rule"], d["line"]) for d in errors], errors


def _expand(source: str) -> str:
    # doeff_hy の import が Hy の importer と macro を用意する(副作用のための import)
    importlib.import_module("doeff_hy")
    return ast.unparse(hy.compiler.hy_compile(hy.read_many(source), "__main__"))


def test_only_the_static_expansion_reads_the_item_through_the_helper() -> None:
    from doeff_hy.static_view import static_view

    runtime = _expand(PROBE)
    with static_view():
        static = _expand(PROBE)
    assert "_doeff_traverse_item" in static, static
    # 実行時の展開は件の引数をそのまま受ける(型検査の補助を名指さない)
    assert "_doeff_traverse_item" not in runtime, runtime
    assert "(text):" in runtime, runtime


def test_for_do_runs_as_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from doeff_traverse import fail_handler, sequential

    from doeff import run, with_handlers

    importlib.import_module("doeff_hy")
    (tmp_path / "for_do_probe_2321.hy").write_text(PROBE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    probe = importlib.import_module("for_do_probe_2321")
    widths = probe.widths
    total_width = probe.total_width
    assert run(with_handlers([sequential(), fail_handler], widths(("ab", "", "cde")))) == (2, 3)
    assert run(with_handlers([sequential(), fail_handler], total_width(("ab", "", "cde")))) == 6
