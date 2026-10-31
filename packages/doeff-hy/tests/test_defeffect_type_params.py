"""defeffect の型の引数 :tp の失敗ケース。

答えが「走らせる Program の答え」で決まる effect(doeff-core-effects の SqlTransaction — 答え = program の答え T | 失敗の値)は、
欄 `program` と :answer が型の引数 T を読む。型検査の展開は class を T について総称にしなかったので、欄に `Program[T, object]` を
書くと定義する module の検が「T がこの文脈で意味を持たない」の赤を出し、書かなければ使い手に答えの T が決まらなかった。
:tp は defk・deff の :tp と同じ形で、class の型の引数を契約の 1 点のまま書く口:

- 型検査の展開では PEP 695 の `class E[T](…)`。.pyi にも型の引数が載り、基底の答えが T を読む。
- 実行時の展開には型の引数を出さない(class の __type_params__ は空)— T は module の TypeVar で読む。
- 名の並びでない :tp・重なる名は断る。
- 失敗ケース: :tp を外すと、定義する module の検が赤になる(T が意味を持たない)。使い手は答えの型の取り違えを赤にできる。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import hy
import pytest

from doeff_hy._static_check import Projection, project
from doeff_hy._static_stub import stub_of, strict_errors

# 型検査の展開と stub が書く型の引数の構文(PEP 695・Hy の :tp)は Python 3.12 から — 3.11 の Hy は :tp を断る。
needs_type_parameter_syntax = pytest.mark.skipif(sys.version_info < (3, 12), reason="型の引数の構文は Python 3.12 から")

PRELUDE = """
(require doeff-hy.macros [defeffect val])
(import doeff [Program])
(import typing [TypeVar])
(val T (TypeVar "T"))
"""

GENERIC = """
(defeffect Wrapped
  "program を包んで走らせる(答え = program の答え | None)。"
  {:tp [T]
   :fields [(: database str) (: program (get Program #(T object)))]
   :pre [(: database str) (: program Program)]
   :answer (| T None)
   :tags {:context "probe" :role "foundation"}})
"""

# :tp を外した形(#2939 の前の書き方): 欄の T は class の型の引数ではなく、定義する module の検が赤にする。
UNDECLARED = GENERIC.replace("{:tp [T]\n   :fields", "{:fields")

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")


def evaluate(source: str) -> dict[str, object]:
    """PRELUDE と source を 1 つの名前空間で評価し、名前空間を返すため。"""
    namespace: dict[str, object] = {"__name__": "effect_type_params_probe"}
    hy.eval(hy.read_many(PRELUDE + source), namespace)
    return namespace


def refused(source: str) -> str:
    """source の評価が断られることを確かめ、誤りの文を返すため。"""
    with pytest.raises(Exception) as caught:
        evaluate(source)
    return str(caught.value)


def _module(tmp_path: Path, body: str) -> Path:
    """検の module を tmp の根の下の package に置くため(根 = tmp_path)。"""
    source = tmp_path / "probe_pkg" / "probe_mod.hy"
    source.parent.mkdir(parents=True, exist_ok=True)
    (source.parent / "__init__.py").write_text("", encoding="utf-8")
    source.write_text(PRELUDE + body, encoding="utf-8")
    return source


def test_the_effect_works_at_runtime_and_the_class_has_no_type_parameter_syntax() -> None:
    wrapped = evaluate(GENERIC)["Wrapped"]
    assert isinstance(wrapped, type)
    # class の __type_params__ は Python 3.12 から(3.11 には型の引数の構文そのものが無い)。
    if sys.version_info >= (3, 12):
        assert wrapped.__type_params__ == ()
    assert wrapped.__doeff_defeffect__ is True


def test_the_runtime_checks_are_unchanged_by_the_type_parameter() -> None:
    wrapped = evaluate(GENERIC)["Wrapped"]
    assert callable(wrapped)
    with pytest.raises(AssertionError):
        wrapped(database="db", program="not a program")


@needs_type_parameter_syntax
def test_the_type_check_view_makes_the_class_generic(tmp_path: Path) -> None:
    projection = project(tmp_path, [tmp_path], _module(tmp_path, GENERIC))
    assert isinstance(projection, Projection)
    lines = projection.text.splitlines()
    assert "class Wrapped[T](_doeff_effect_base):" in lines
    assert "    program: Program[T, object]" in lines


@needs_type_parameter_syntax
def test_the_stub_carries_the_type_parameter_and_the_answer_reads_it(tmp_path: Path) -> None:
    made = stub_of(tmp_path, [tmp_path], _module(tmp_path, GENERIC))
    lines = made.text.splitlines()
    assert "class Wrapped[T](_doeff_effect_base[T | None]):" in lines
    assert "    program: Program[T, object]" in lines
    assert made.incomplete == ()


def test_without_the_type_parameter_the_class_is_not_generic(tmp_path: Path) -> None:
    # 失敗ケース: :tp が無ければ展開は class を総称にしない(定義する module の検が、欄の T を意味のない型の引数と読む)。
    projection = project(tmp_path, [tmp_path], _module(tmp_path, UNDECLARED))
    assert isinstance(projection, Projection)
    assert "class Wrapped(_doeff_effect_base):" in projection.text.splitlines()


@needs_pyright
def test_the_defining_module_has_no_red_with_the_type_parameter_and_one_without(tmp_path: Path) -> None:
    (tmp_path / "probe_pkg").mkdir()
    (tmp_path / "probe_pkg" / "__init__.py").write_text("", encoding="utf-8")
    declared = strict_errors(tmp_path, PRELUDE + GENERIC)
    undeclared = strict_errors(tmp_path, PRELUDE + UNDECLARED)
    assert not [e for e in declared if "no meaning" in e], declared
    assert [e for e in undeclared if "no meaning" in e], undeclared


USER = """\
(require doeff-hy.macros [defk <-])
(import doeff [Pure])
(import probe_pkg.probe_mod [Wrapped])

(defk answer-of []
  {:pre [] :post [(: % ANSWER)]}
  "program の答えを呼び手の型で受けるため。"
  (<- answer ANSWER (Wrapped :database "db" :program (Pure 1)))
  answer)
"""


@needs_pyright
@needs_type_parameter_syntax
def test_the_program_answer_type_reaches_the_caller(tmp_path: Path) -> None:
    source = _module(tmp_path, GENERIC)
    # 使い手が読む型は .hy の隣の .pyi(型検査の展開の基底は素のまま — 答えを運ぶのは .pyi だけ)。
    source.with_suffix(".pyi").write_text(stub_of(tmp_path, [tmp_path], source).text, encoding="utf-8")
    # 使い手: program の答え int が T になり、effect の答えは `int | None`。同じ型で受けると緑、str で受けると赤。
    assert strict_errors(tmp_path, USER.replace("ANSWER", "(| int None)")) == ()
    assert [e for e in strict_errors(tmp_path, USER.replace("ANSWER", "str")) if "reportAssignmentType" in e]


@pytest.mark.parametrize("value", ["T", "[]", '["T"]', "[T T]"])
def test_type_parameters_must_be_distinct_names(value: str) -> None:
    broken = GENERIC.replace("{:tp [T]", f"{{:tp {value}")
    assert ":tp" in refused(broken)
