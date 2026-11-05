"""Tests that for one Traverse, sequential reads the inner handlers only once, when it runs the first item.

Traverse items run under the handlers (inner handlers) that sit between where Traverse was emitted and sequential. sequential
reads them from the continuation (GetHandlers) and reinstalls them per item. It used to read even for a Traverse where no item runs (0 items, a Collection of only failed items),
and the read went through one level of calling the @do function get_inner_handlers (about 1.1 µs of the roughly 5.8 µs fixed cost of one Traverse with 0 items
— #2927). The rule "the last one = remove the received handler itself" lives in one place, doeff.handler_utils.inner_of,
and the count is observed at that call.
"""

from collections.abc import Iterable

import pytest
from doeff_traverse import Collection, ItemResult, Traverse, sequential

from doeff import EffectBase, Resume, do, handler_utils, run
from doeff import handler as program_handler


class Ask(EffectBase):
    """An effect that asks the inner handler from inside an item."""


@do
def answer(effect: Ask, k):
    return (yield Resume(k, 7))


@do
def plus_ask(x: int):
    asked = yield Ask()
    return x + asked


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """For each call of inner_of (= a read of the inner handlers), accumulates the number of handlers read."""
    seen: list[int] = []
    inner_of = handler_utils.inner_of

    def counting(handlers: list[object]) -> list[object]:
        seen.append(len(handlers))
        return inner_of(handlers)

    monkeypatch.setattr(handler_utils, "inner_of", counting)
    return seen


def traverse_under_answer(items: Iterable[int]) -> Collection[int]:
    """Installs in the order sequential -> answer (inner handler) -> Traverse and runs."""

    @do
    def body():
        return (yield Traverse(plus_ask, items))

    return run(sequential(program_handler(answer)(body())))


def test_inner_of_drops_the_catching_handler() -> None:
    assert handler_utils.inner_of(["inner", "middle", "catcher"]) == ["inner", "middle"]
    assert handler_utils.inner_of([]) == []


def test_a_traverse_reads_the_inner_handlers_once_for_all_its_items(reads: list[int]) -> None:
    col = traverse_under_answer([1, 2, 3])
    assert col.valid_values == [8, 9, 10], "items run under the inner handler (answer)"
    assert len(reads) == 1, "even with 3 items, it reads only once"


def test_a_traverse_with_no_item_reads_no_handlers(reads: list[int]) -> None:
    col = traverse_under_answer([])
    assert len(col) == 0
    assert reads == [], "with no item to run, the inner handlers are not read"


def test_a_traverse_of_failed_items_only_reads_no_handlers(reads: list[int]) -> None:
    failed: Collection[int] = Collection([ItemResult(index=0, value=ValueError("earlier"), failed=True)])
    col = traverse_under_answer(failed)
    assert len(col.failed_items) == 1, "a failed item is carried through as is"
    assert reads == [], "with only failed items there is no item to run, so no read"
