"""on-raise の展開が、受けごとの関数の引数に型の注記を付ける(pyright strict の「引数の型が分からない」を出さない)。

実行時の意味は doeff-core-effects の test_outcomes.hy が持つ。ここは展開の字面だけを見る: 注記が無いと、on-raise を書いた
呼び手ごとに「Type of parameter が unknown」「Type annotation is missing」の赤が 5 件ずつ出た(card ki-646811f7dc36)。
"""

from __future__ import annotations

from types import ModuleType

import hy
from hy.models import Expression, Sequence, Symbol

import doeff_hy  # noqa: F401 - Hy の import hook を登録する


def _expand(source: str) -> object:
    module = ModuleType("doeff_hy_on_raise_expansion_probe")
    hy.eval(hy.read("(require doeff-hy.macros [on-raise])"), module=module)
    return hy.macroexpand(hy.read(source), module)


def _annotated_object_params(tree: object) -> list[object]:
    """`(fn [#^ object 名] …)` の引数の並びを集める。"""
    found: list[object] = []
    if isinstance(tree, Expression) and tree and tree[0] == Symbol("fn"):
        found.append(tree[1])
    if isinstance(tree, Sequence):
        for child in tree:
            found.extend(_annotated_object_params(child))
    return found


def test_on_raise_annotates_the_reason_parameter_of_each_case() -> None:
    tree = _expand("(on-raise body (Unreachable detail) detail (Refusal why) why)")
    params = _annotated_object_params(tree)
    assert len(params) == 2, params
    for params_list in params:
        assert "object" in repr(params_list), params_list
        assert "annotate" in repr(params_list).lower(), params_list
