"""契約の型の引数 :tp(agora-redesign #2893)の失敗ケース。

答えが引数の型で決まる関数(本体の答えをそのまま返す包みなど)は、契約の型を実行時に isinstance で確かめるので総称を書けず、
`:post` を説明の文字列にしていた — 道具 static_stub の .pyi は答えを Incomplete にし、使い手の型検査は答えの型を失った
(doeff-records の records-connected・doeff-cluster の with-cluster-handlers)。:tp は契約の 1 点のまま総称を書く口:

- 型検査の展開では PEP 695 の `def f[T]`。.pyi にも型の引数が載り、答えは Incomplete にならない。
- 実行時の確かめでは型の引数を object に消す — どの型の答えも通り、外側の型(tuple・Program)は今までどおり断る。失敗の文は書いた型。
- 実行時の展開に PEP 695 の綴りを出さない(関数の __type_params__ は空)。
- deff・defk だけが受ける(defp は断る)。名の並びでない :tp は断る。
- 失敗ケース: :tp を外して説明の文字列に戻すと、答えは Incomplete に戻る。
"""

from __future__ import annotations

from pathlib import Path

import hy
import pytest
from doeff import Pure, run

from doeff_hy.static_check import Projection, project
from doeff_hy.static_stub import stub_of

PRELUDE = """
(require doeff-hy.macros [defk deff defp <-])
(import doeff [Program])
"""

GENERIC = """
(defk passed-through [body]
  {:tp [T] :pre [(: body (of Program T object))] :post [(: % T)]}
  "本文の答えをそのまま返すため。"
  (<- answer body)
  answer)

(deff first-of [items]
  {:tp [T] :pre [(: items (of tuple T ...))] :post [(: % T)]}
  "並びの最初の要素を返すため。"
  (get items 0))
"""

# :tp を外し、答えを説明の文字列に戻した形(#2893 の前の書き方)。
DESCRIBED = GENERIC.replace(
    "{:tp [T] :pre [(: body (of Program T object))] :post [(: % T)]}",
    '{:pre [(: body Program)] :post [(: % "本文の答え")]}',
)


def evaluate(source: str) -> dict[str, object]:
    """PRELUDE と source を 1 つの名前空間で評価し、名前空間を返すため。"""
    namespace: dict[str, object] = {"__name__": "type_params_probe"}
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


def answer_of(namespace: dict[str, object], form: str) -> object:
    """namespace の上で Hy の式 1 つを評価した答えを返すため(定義を Program として走らせる式を Hy の側で書く)。"""
    return hy.eval(hy.read(form), namespace)


def test_any_answer_type_passes_at_runtime() -> None:
    ns = evaluate(GENERIC) | {"run": run, "Pure": Pure}
    assert answer_of(ns, "(run (passed-through (Pure 42)))") == 42
    assert answer_of(ns, '(run (passed-through (Pure "x")))') == "x"
    assert answer_of(ns, '(first-of #("a" 1))') == "a"


def test_the_outer_type_is_still_checked_and_named_as_written() -> None:
    first_of = evaluate(GENERIC)["first_of"]
    assert callable(first_of)
    with pytest.raises(AssertionError) as caught:
        first_of(["a"])
    message = str(caught.value)
    assert "Symbol('T')" in message and "got list" in message


def test_the_runtime_expansion_has_no_type_parameter_syntax() -> None:
    ns = evaluate(GENERIC)
    assert getattr(ns["first_of"], "__type_params__") == ()


def test_the_type_check_view_declares_the_type_parameters(tmp_path: Path) -> None:
    projection = project(tmp_path, [tmp_path], _module(tmp_path, GENERIC))
    assert isinstance(projection, Projection)
    lines = projection.text.splitlines()
    assert "def passed_through[T](body: 'Program[T, object]') -> 'T':" in lines
    assert "def first_of[T](items: 'tuple[T, ...]') -> 'T':" in lines


def test_the_stub_carries_the_type_parameters(tmp_path: Path) -> None:
    made = stub_of(tmp_path, [tmp_path], _module(tmp_path, GENERIC))
    lines = made.text.splitlines()
    assert "def passed_through[T](body: Program[T, object]) -> _Program[T, object]:" in lines
    assert "def first_of[T](items: tuple[T, ...]) -> T:" in lines
    assert made.incomplete == ()


def test_without_the_type_parameter_the_answer_falls_back_to_incomplete(tmp_path: Path) -> None:
    # 失敗ケース: 説明の文字列の答えは型を運ばない。
    made = stub_of(tmp_path, [tmp_path], _module(tmp_path, DESCRIBED))
    assert made.incomplete == ("passed_through の答え",)


def test_defp_refuses_type_parameters() -> None:
    assert ":tp" in refused("(defp answer {:tp [T] :post [(: % int)]} 42)")


@pytest.mark.parametrize("value", ["T", "[]", '["T"]', "[T T]"])
def test_type_parameters_must_be_distinct_names(value: str) -> None:
    assert ":tp" in refused(f"(deff f [x] {{:tp {value} :pre [(: x int)] :post [(: % int)]}} x)")
