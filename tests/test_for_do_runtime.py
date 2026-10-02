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
from doeff_traverse.collection import Collection
from doeff_traverse.effects import Traverse
from doeff_traverse.handlers import sequential

from doeff import Pass, do, run
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


def _eval(code: str) -> object:
    """for/do を含む Hy の式を、for/do の展開が引く名を import した module で評価した値(最後の式)。"""
    mod = types.ModuleType("for_do_runtime")
    sys.modules["for_do_runtime"] = mod
    mod.__dict__["half"] = _half
    hy.macros.require("doeff_hy.macros", mod, assignments=[["for/do", "for/do"], ["<-", "<-"]])
    result = None
    for form in hy.read_many(_HEADER + code):
        result = hy.eval(form, mod.__dict__, module=mod)
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
