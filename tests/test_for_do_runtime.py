"""Tests for the runtime shape of for/do.

The for/do expansion wraps the per-item function with @do just once per Traverse (wrapping again per item cost about 7 us of @do
construction per item). The item Program is the same as before (a single call taking the item as an argument), so the answer, items that fail on When, labels, and item failures
must be the same as before. The last test checks that the function passed to Traverse is the one function wrapped with @do -- going back to the
per-item rewrap shape turns it red.
"""

import sys
import types

import doeff_hy  # noqa: F401 — registers extensions
import hy
import hy.macros
import pytest
from doeff_core_effects import scheduled
from doeff_traverse import Collection, Traverse, parallel, parallel_fail_fast, sequential

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
    """The value (the last expression -- a Program or effect) of a Hy expression containing for/do, evaluated in a module that imports the names the for/do expansion pulls in."""
    mod = types.ModuleType("for_do_runtime")
    sys.modules["for_do_runtime"] = mod
    mod.__dict__["half"] = _half
    hy.macros.require("doeff_hy.macros", mod, assignments=[["for/do", "for/do"], ["<-", "<-"]])
    result = None
    for form in hy.read_many(_HEADER + code):
        result = hy.eval(form, mod.__dict__, module=mod)
    # The runtime Program is a DoExpr, and the effect (Traverse) is outside it
    assert isinstance(result, (Program, EffectBase)), result
    return result


def _collect(code: str) -> Collection:
    """The answer from running a for/do expression (a Traverse effect) under sequential."""
    return run(sequential(_eval(code)))


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
    collection = run(sequential(program_handler(watch)(program)))
    assert collection.valid_values == [0, 0, 1]
    [step] = seen
    assert hasattr(step, "__doeff_generator_function__"), "the Traverse function is not wrapped with @do (the shape that rewraps per item)"


# A shape with two or more Froms stacked in for/do. Stacked Froms, like a list comprehension or SQL CROSS JOIN, flatly list the body's value as one item for each
# pair of an outer item and an inner item. Previously the outer item's function returned the second From's Traverse as a value without executing it, and
# the inner Traverse effect ended up in valid_values.


def test_two_froms_give_one_value_per_pair() -> None:
    collection = _collect("(for/do (<- a (From [1 2])) (<- b (From [10 20])) (+ a b))")
    assert collection.valid_values == [11, 21, 12, 22]


def test_three_froms_give_one_value_per_triple() -> None:
    collection = _collect("(for/do (<- a (From [1 2])) (<- b (From [10 20])) (<- c (From [100 200])) (+ a b c))")
    assert collection.valid_values == [111, 211, 121, 221, 112, 212, 122, 222]


def test_skips_and_failures_of_both_froms_keep_their_places() -> None:
    # The inner items are built from the outer item. An item dropped by the outer When stays as one item at the outer position, and an inner item's failure keeps the outer and inner history in sequence.
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
    from doeff_core_effects import scheduled
    from doeff_traverse import parallel

    program = _eval("(for/do (<- a (From [1 2])) (<- b (From [10 20])) (+ a b))")
    collection = run(scheduled(parallel(concurrency=2)(program)))
    assert collection.valid_values == [11, 21, 12, 22]


# The Traverse handler contract (blind A of the design review): the outer item's program of stacked Froms performs the inner Traverse
# again to the same handler. The handler runs the item program under itself and does not keep resources spanning Traverses (such as a one-slot frame in the handler) across
# item programs. A handler that keeps them stops waiting for the inner items while the outer item holds the slot (it always stops with one slot).
# Run all shipped handlers here, so that any change to this shape turns it red.
_SHIPPED_HANDLERS = {
    "sequential": lambda: sequential,
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
