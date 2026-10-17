"""for/do の実行時の形の検(agora-redesign #2871)。

for/do の展開は、件ごとの関数を Traverse 1 つに 1 度だけ @do で包む(件ごとに包み直すと、件 1 つあたり約 7 µs の @do の
組み立てがかかっていた)。件の Program は前と同じ(件を引数に受ける 1 つの呼び)なので、答え・When で落ちる件・label・件の失敗は
前と同じでなければならない。最後の検は、Traverse に渡る関数が @do で包んだ 1 つの関数である事を見る — 件ごとに
包み直す形へ戻ると赤になる。
"""

import sys
import types

import doeff_hy  # noqa: F401 — registers extensions
import hy
import hy.macros
import pytest
from doeff_core_effects.scheduler import scheduled
from doeff_traverse.collection import Collection
from doeff_traverse.effects import Traverse
from doeff_traverse.handlers import parallel, parallel_fail_fast, sequential

from doeff import EffectBase, Pass, Program, do, run
from doeff import handler as program_handler

_HEADER = """
(import doeff [do :as _doeff-do])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse [Skip :as _doeff_traverse_Skip])
"""


@do
def _half(x: int):
    if x == 2:
        raise ValueError("two")
    return x // 2


def _eval(code: str) -> Program:
    """for/do を含む Hy の式を、for/do の展開が引く名を import した module で評価した値(最後の式 — Program か effect)。"""
    mod = types.ModuleType("for_do_runtime")
    sys.modules["for_do_runtime"] = mod
    mod.__dict__["half"] = _half
    hy.macros.require("doeff_hy.macros", mod, assignments=[["for/do", "for/do"], ["<-", "<-"]])
    result = None
    for form in hy.read_many(_HEADER + code):
        result = hy.eval(form, mod.__dict__, module=mod)
    # 実行時の Program は DoExpr で、effect(Traverse)はその外
    assert isinstance(result, (Program, EffectBase)), result
    return result


def _collect(code: str) -> Collection:
    """for/do の式(Traverse の effect)を sequential() の下で走らせた答え。"""
    return run(sequential()(_eval(code)))


def test_values_skips_and_failures_keep_their_places() -> None:
    collection = _collect("(for/do (<- x (From [0 1 2 3 4])) (When (!= x 1)) (<- y (half x)) y)")
    assert collection.valid_values == [0, 1, 2]
    assert [(item.index, [entry.event for entry in item.history]) for item in collection.failed_items] == [
        (1, ["skipped"]),
        (2, ["failed"]),
    ]
    assert [str(error) for error in collection.errors] == ["two"]


def test_a_label_reaches_the_history() -> None:
    collection = _collect('(for/do (<- x (From [3 5] :label "halve")) (<- y (half x)) y)')
    assert collection.valid_values == [1, 2]
    assert {entry.stage for item in collection.all_items for entry in item.history} == {"halve"}


def test_the_step_is_one_do_function_per_traverse() -> None:
    seen: list[object] = []

    @do
    def watch(effect: Traverse, k):
        seen.append(effect.f)
        yield Pass(effect, k)

    program = _eval("(for/do (<- x (From [0 1 3])) (<- y (half x)) y)")
    collection = run(sequential()(program_handler(watch)(program)))
    assert collection.valid_values == [0, 0, 1]
    [step] = seen
    assert hasattr(step, "__doeff_generator_function__"), "Traverse の関数が @do で包まれていない(件ごとに包み直す形)"


# for/do に From を 2 つ以上重ねた形(card ki-7e6a11f56bd5)。重ねた From は list の内包・SQL の CROSS JOIN と同じく、外の件と
# 内の件の組ごとに本体の値を 1 件として平らに並べる。前は 2 つ目の From の Traverse を外の件の関数が実行せずに値として返し、
# valid_values に内側の Traverse の effect が入っていた。


def test_two_froms_give_one_value_per_pair() -> None:
    collection = _collect("(for/do (<- a (From [1 2])) (<- b (From [10 20])) (+ a b))")
    assert collection.valid_values == [11, 21, 12, 22]


def test_three_froms_give_one_value_per_triple() -> None:
    collection = _collect("(for/do (<- a (From [1 2])) (<- b (From [10 20])) (<- c (From [100 200])) (+ a b c))")
    assert collection.valid_values == [111, 211, 121, 221, 112, 212, 122, 222]


def test_skips_and_failures_of_both_froms_keep_their_places() -> None:
    # 内の items は外の件から作る。外の When で落ちた件は 1 件のまま外の位置に残り、内の件の失敗は外と内の履歴を続けて持つ。
    collection = _collect(
        '(for/do (<- a (From [0 1 2] :label "outer")) (When (!= a 1))'
        ' (<- b (From (range a 3) :label "inner")) (<- y (half b)) #(a y))'
    )
    assert collection.valid_values == [(0, 0), (0, 0)]
    assert [
        (item.index, item.failed, [(entry.stage, entry.event) for entry in item.history])
        for item in collection.all_items
    ] == [
        (0, False, [("outer", "ok"), ("inner", "ok")]),
        (1, False, [("outer", "ok"), ("inner", "ok")]),
        (2, True, [("outer", "ok"), ("inner", "failed")]),
        (3, True, [("outer", "skipped")]),
        (4, True, [("outer", "ok"), ("inner", "failed")]),
    ]
    assert [str(error) for error in collection.errors] == ["two", "two"]


def test_two_froms_under_the_parallel_handler_give_the_same_values() -> None:
    from doeff_core_effects.scheduler import scheduled
    from doeff_traverse.handlers import parallel

    program = _eval("(for/do (<- a (From [1 2])) (<- b (From [10 20])) (+ a b))")
    collection = run(scheduled(parallel(concurrency=2)(program)))
    assert collection.valid_values == [11, 21, 12, 22]


# Traverse の handler の契約(設計検証 lt-BD5XYC8Z64P5YMHZDEAPKXQXK5 の盲検 A): 重ねた From の外の件の program は、内側の Traverse を
# 同じ handler へもう一度出す。handler は件の program を自分の下で走らせ、Traverse をまたぐ資源(handler に 1 つの枠など)を件の
# program の間に持ち続けない。持ち続ける handler は、外の件が枠を占めたまま内の件を待って止まる(枠 1 で必ず止まる)。
# 出荷する handler の全部をここで回し、どれかがこの形に変わると赤になる。
_SHIPPED_HANDLERS = {
    "sequential": lambda: sequential(),
    "parallel-1": lambda: parallel(concurrency=1),
    "parallel-2": lambda: parallel(concurrency=2),
    "parallel_fail_fast-1": lambda: parallel_fail_fast(concurrency=1),
    "parallel_fail_fast-2": lambda: parallel_fail_fast(concurrency=2),
}


@pytest.mark.parametrize("name", sorted(_SHIPPED_HANDLERS))
def test_nested_froms_answer_under_every_shipped_handler(name: str) -> None:
    handler = _SHIPPED_HANDLERS[name]
    two = _eval("(for/do (<- a (From [1 2 3])) (<- b (From [10 20])) (+ a b))")
    three = _eval("(for/do (<- a (From [1 2])) (<- b (From [10 20])) (<- c (From [100 200])) (+ a b c))")
    assert run(scheduled(handler()(two))).valid_values == [11, 21, 12, 22, 13, 23]
    assert run(scheduled(handler()(three))).valid_values == [111, 211, 121, 221, 112, 212, 122, 222]
