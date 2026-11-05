"""Tests the contract that sequential leaves an element's failure on the element.

An element's failure is caught by a try inside the handler, not by a per-element Try effect (60% of the per-element cost was the Try round trip).
Even after changing how it is caught, the following 4 must stay the same as before: an element's exception leaves the exception and history as a failed element; an effect inside the element
that nobody answers also becomes a failed element; a failure of a nested Traverse stays in the inner Collection; a Try inside an element can be answered even without
an outer try_handler (previously a per-element Try put try_handler outside the element).
"""

from collections.abc import Callable
from typing import TypeVar

from doeff_core_effects import Try
from doeff_traverse import Collection, Traverse, sequential

from doeff import EffectBase, Err, Ok, Program, UnhandledEffect, do, run

_Answer = TypeVar("_Answer")


class _Nobody(EffectBase[int]):
    """An effect that no handler answers (emitting it inside an element makes an unhandled failure)."""


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
    """The answer of running Traverse once with only sequential installed (no try_handler outside)."""

    @do
    def program():
        return (yield Traverse(f, items))

    return run(sequential(program()))


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
