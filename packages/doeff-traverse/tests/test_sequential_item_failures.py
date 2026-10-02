"""sequential() が要素の失敗を要素に残す契約の検(agora-redesign #2871)。

要素の失敗は、要素ごとの Try effect ではなく handler の中の try で受ける(要素 1 つあたりの費用の 6 割が Try の往復だった)。
受け方を変えても次の 4 つは前と同じでなければならない: 要素の例外は失敗した要素として例外と履歴を残す・要素の中の
誰も答えない effect も失敗した要素になる・入れ子の Traverse の失敗は内側の Collection に留まる・要素の中の Try は外に
try_handler が無くても答えられる(前は要素ごとの Try が try_handler を要素の外側に入れていた)。
"""

from collections.abc import Callable
from typing import TypeVar

from doeff_core_effects.effects import Try
from doeff_traverse.collection import Collection
from doeff_traverse.effects import Traverse
from doeff_traverse.handlers import sequential
from doeff_vm import EffectBase, Err, Ok, UnhandledEffect

from doeff import Program, do, run

_Answer = TypeVar("_Answer")


class _Nobody(EffectBase[int]):
    """どの handler も答えない effect(要素の中で出すと未処理の失敗になる)。"""


@do
def _half(x: int):
    if x == 2:
        raise ValueError("two")
    if False:
        yield
    return x // 2


@do
def _asks_nobody(x: int):
    if x == 1:
        return (yield _Nobody())
    return x


@do
def _tries(x: int):
    return (yield Try(_half(x)))


@do
def _inner(x: int):
    collection = yield Traverse(_half, list(range(x)))
    return (collection.valid_values, [str(error) for error in collection.errors])


def _traverse(f: Callable[[int], Program[_Answer]], items: list[int]) -> Collection[_Answer]:
    """sequential() だけを入れて Traverse を 1 回走らせた答え(try_handler を外に入れない)。"""

    @do
    def program():
        return (yield Traverse(f, items))

    return run(sequential()(program()))


def test_an_item_exception_stays_as_a_failed_item_with_its_history() -> None:
    collection = _traverse(_half, [0, 2, 4])
    assert collection.valid_values == [0, 2]
    [failed] = collection.failed_items
    assert failed.index == 1
    assert isinstance(failed.value, ValueError)
    assert [(entry.event, entry.detail) for entry in failed.history] == [("failed", "two")]


def test_an_unhandled_effect_in_an_item_fails_only_that_item() -> None:
    collection = _traverse(_asks_nobody, [0, 1, 2])
    assert collection.valid_values == [0, 2]
    assert [type(error) for error in collection.errors] == [UnhandledEffect]


def test_a_nested_traverse_keeps_its_failures_inside() -> None:
    collection = _traverse(_inner, [1, 3])
    assert collection.valid_values == [([0], []), ([0, 0], ["two"])]
    assert collection.failed_items == []


def test_a_try_in_an_item_is_answered_without_an_outer_try_handler() -> None:
    collection = _traverse(_tries, [0, 2])
    assert [type(result) for result in collection.valid_values] == [Ok, Err]
    assert collection.failed_items == []
