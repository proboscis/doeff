"""#2812 の旧 A 比較用実行仕様。一般的な推奨は撤回済み。

公開 Sequence の実装計画ではなく、旧案と基点の handler の差を保存する。
参照実装の可変 list はテスト内だけで、production の規約変更を含まない。
"""
from collections.abc import Generator
from typing import Any, TypeVar

import pytest
from doeff_core_effects.handlers import reader
from doeff_traverse.effects import Inspect, Traverse
from doeff_traverse.handlers import sequential

from doeff import Ask, Program, Pure, do, run

T = TypeVar("T")
E = TypeVar("E")


@do
def reference_sequence(*programs: Program[T, E]) -> Generator[E, Any, tuple[T, ...]]:
    values: list[T] = []
    for program in programs:
        values.append((yield from program))
    return tuple(values)


@pytest.fixture
def sequence_factory():
    return reference_sequence


def test_empty_requires_no_handlers(sequence_factory):
    assert run(sequence_factory()) == ()


def test_order_duplicates_and_none(sequence_factory):
    assert run(sequence_factory(Pure(2), Pure(None), Pure(2), Pure(1))) == (2, None, 2, 1)


def test_children_are_lazy_and_finish_before_next_starts(sequence_factory):
    events = []

    @do
    def child(index):
        events.append((index, "start"))
        value = yield Pure(index)
        events.append((index, "end"))
        return value

    program = sequence_factory(child(1), child(2))
    assert events == []
    assert run(program) == (1, 2)
    assert events == [(1, "start"), (1, "end"), (2, "start"), (2, "end")]


def test_first_error_propagates_and_later_child_never_runs(sequence_factory):
    events = []
    failure = ValueError("row failed")

    @do
    def child(index):
        events.append(index)
        if index == 2:
            raise failure
        return index

    with pytest.raises(ValueError, match="row failed") as caught:
        run(sequence_factory(child(1), child(2), child(3)))
    assert caught.value is failure
    assert events == [1, 2]


def test_children_use_callers_reader_and_nested_reader(sequence_factory):
    @do
    def ask_name():
        return (yield Ask("name"))

    program = sequence_factory(
        ask_name(), reader({"name": "inner"})(ask_name()), ask_name()
    )
    assert run(reader({"name": "outer"})(program)) == ("outer", "inner", "outer")


def test_unhandled_child_effect_is_not_swallowed(sequence_factory):
    events = []

    @do
    def later():
        events.append("later")
        return 2

    @do
    def ask_missing():
        return (yield Ask("missing"))

    # Runtime exception class is intentionally not a proposed Sequence API decision.
    with pytest.raises(Exception, match=r"(?i)(unhandled|handler)"):
        run(sequence_factory(ask_missing(), later()))
    assert events == []


def test_nested_sequences_preserve_tuple_shape(sequence_factory):
    assert run(sequence_factory(sequence_factory(Pure(1)), Pure(2))) == ((1,), 2)


def test_rerun_gets_a_fresh_accumulator(sequence_factory):
    program = sequence_factory(Pure(1), Pure(2))
    assert run(program) == (1, 2)
    assert run(program) == (1, 2)


def test_existing_traverse_continues_after_failure():
    """基点の sequential handler と旧 A の差。Traverse 全体の制約ではない。"""
    events = []
    failure = ValueError("row failed")

    @do
    def child(index):
        events.append(index)
        if index == 2:
            raise failure
        return index

    @do
    def program():
        collection = yield Traverse(child, [1, 2, 3])
        return (yield Inspect(collection))

    items = run(sequential()(program()))
    assert events == [1, 2, 3]
    assert [item.failed for item in items] == [False, True, False]
    assert items[1].value is failure
