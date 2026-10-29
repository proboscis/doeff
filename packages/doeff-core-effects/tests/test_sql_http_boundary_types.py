"""sql_effects・http_effects の型の境目の失敗ケース(agora-redesign #2939 — 手の .pyi を道具の出力に置き換えた時に決めた境目)。

- SqlTransaction は defeffect の型の引数 :tp [T] で総称になり、program の答え T が使い手の `<-` の答えに通る(型の宣言 .pyi も
  `class SqlTransaction[T](EffectBase[T | SqlFailed | SqlUnreachable])`)。通らなければ、取り違えた答えの型が赤にならない。
- HttpRequest の params・body は外の HTTP へ渡す任意の値で、境目の型は `Mapping[str, object]`(値について共変 — 使い手の
  `dict[str, str]` はそのまま入る。`dict[str, object]` は不変で入らず、`Any` は型の逃げ)。dict でない mapping はその場で dict にし、
  dict と mapping でない値(httpx が受ける組の列など)は今までどおりそのまま持つ。
- driver の値の型は実行時に検め、検めた結果を型の述語(TypeGuard)で静的な型へ運ぶ(cast を使わない)。
"""

import shutil
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
import pytest

from doeff_core_effects.http_effects import HttpRequest
from doeff_core_effects.sql_effects import is_driver_value, is_plain_rows
from doeff_hy._static_stub import strict_errors

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")


def test_http_request_keeps_a_dict_and_turns_another_mapping_into_one() -> None:
    fields = {"a": 1}
    kept = HttpRequest("GET", "http://x", params=fields, body=fields)
    assert kept.params is fields and kept.body is fields
    turned = HttpRequest("POST", "http://x", params=MappingProxyType(fields), body=MappingProxyType(fields))
    assert type(turned.params) is dict and type(turned.body) is dict
    assert turned.params == fields and turned.body == fields


def test_http_request_leaves_values_that_are_not_mappings_as_they_are() -> None:
    # 組の列の params(同じ名を重ねる query)は dict にすると重なりが消える — mapping だけを dict にする。
    repeated = [("a", "1"), ("a", "2")]
    untyped_caller: Callable[..., HttpRequest] = HttpRequest  # 型の外から呼ぶ手(引数の型を見ない)で、今までの呼びが通る確かめ
    request = untyped_caller("GET", "http://x", params=repeated, body=b"raw")
    assert request.params == repeated and request.body == b"raw"


def test_the_predicates_carry_the_runtime_check_to_the_type() -> None:
    assert is_plain_rows([(1, "a", None, 1.5, b"x", True)]) is True
    assert is_plain_rows(((1, Decimal("2.5")),)) is False
    assert is_driver_value(Decimal("2.5")) is True
    assert is_driver_value(object()) is False


HTTP_FLOW = """\
(import doeff_core_effects.http_effects [HttpRequest])

(val #^ (get dict #(str str)) strings {"a" "b"})
(val #^ (get dict #(str int)) numbers {"a" 1})
(val request (HttpRequest "POST" "http://x" :params strings :body numbers))
(val other (HttpRequest "POST" "http://x" :params numbers :body strings))
"""


@needs_pyright
def test_http_request_accepts_a_callers_dict_with_any_value_type(tmp_path: Path) -> None:
    assert strict_errors(tmp_path, "(require doeff-hy.macros [val])\n" + HTTP_FLOW) == ()


@needs_pyright
def test_http_request_refuses_a_params_that_is_not_a_mapping(tmp_path: Path) -> None:
    # 失敗ケース: 境目の型が Mapping でなくなる(object や Any)と、この呼びが赤にならない。
    bad = "(require doeff-hy.macros [val])\n(import doeff_core_effects.http_effects [HttpRequest])\n(val request (HttpRequest \"GET\" \"http://x\" :params 3))\n"
    assert [e for e in strict_errors(tmp_path, bad) if "reportArgumentType" in e]


SQL_FLOW = """\
(require doeff-hy.macros [defk <-])
(import doeff [Pure])
(import doeff_core_effects.sql_effects [SqlTransaction SqlFailed SqlUnreachable])

(defk answer-of []
  {:pre [] :post [(: % ANSWER)]}
  "program の答えを呼び手の型で受けるため。"
  (<- answer ANSWER (SqlTransaction :database "db" :program (Pure 1)))
  answer)
"""


@needs_pyright
def test_the_program_answer_type_reaches_the_caller_of_a_sql_transaction(tmp_path: Path) -> None:
    assert strict_errors(tmp_path, SQL_FLOW.replace("ANSWER", "(| int SqlFailed SqlUnreachable)")) == ()
    # 失敗ケース: program の答えが int なのに str で受けると赤(T が使い手に決まっている)。
    assert [e for e in strict_errors(tmp_path, SQL_FLOW.replace("ANSWER", "(| str SqlFailed SqlUnreachable)")) if "reportAssignmentType" in e]
