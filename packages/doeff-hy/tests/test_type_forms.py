"""runtime_type_form — 型の式を実行時の isinstance の第 2 引数へ写す公開の 1 点(#3366 の根 2)。

macros.hy の契約と `<-` の束縛、外の道具(品質検査の Hy の投影)が同じ関数を呼ぶ。写した form を実際に eval して
isinstance に渡せる事と、写さない form が同じ object のまま返る事を確かめる。
"""

import hy
import pytest
from hy.models import Expression, Object, Symbol

from doeff_hy.type_forms import runtime_type_form


def mapped(source: str) -> Object:
    """型の式の source を写した form(Hy の model は中身で比べられる — hy.repr は `(. None __class__)` を綴りの上で崩すので
    綴りでは比べない)。"""
    return runtime_type_form(hy.read(source))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("None", "(. None __class__)"),
        ("#(int None)", "#(int (. None __class__))"),
        ("(get tuple #(int int))", "tuple"),
        ("(of tuple str ...)", "tuple"),
        ("(of dict str int)", "dict"),
        ("(get dict #(str int))", "dict"),
        ("(| (get tuple #(str ...)) None)", "(| tuple None)"),
        ("(| int None)", "(| int None)"),
        ("#((get tuple #(int int)) None)", "#(tuple (. None __class__))"),
    ],
)
def test_a_type_form_is_mapped_to_what_isinstance_accepts(source: str, expected: str) -> None:
    assert mapped(source) == hy.read(expected)


@pytest.mark.parametrize(
    ("source", "value", "holds"),
    [
        ("None", None, True),
        ("None", 0, False),
        ("(get tuple #(int int))", (1, 2), True),
        ("(get tuple #(int int))", [1, 2], False),
        ("(| (get dict #(str int)) None)", None, True),
        ("#(int None)", None, True),
    ],
)
def test_the_mapped_form_is_accepted_by_isinstance(source: str, value: object, holds: bool) -> None:
    # 写す前の (get tuple #(int int)) は isinstance が断る(parameterized generic)— 写した form は受ける。展開と同じく
    # isinstance の呼びごと Hy で評価する。
    check: Expression = Expression([Symbol("isinstance"), Symbol("value"), runtime_type_form(hy.read(source))])
    assert bool(hy.eval(check, {"value": value})) is holds


@pytest.mark.parametrize("source", ["int", "pd.DataFrame", "(| int str)", "\"str\""])
def test_a_form_that_needs_no_mapping_is_returned_as_the_same_object(source: str) -> None:
    form: Object = hy.read(source)
    if source == "(| int str)":
        assert mapped(source) == hy.read(source)
    else:
        assert runtime_type_form(form) is form
