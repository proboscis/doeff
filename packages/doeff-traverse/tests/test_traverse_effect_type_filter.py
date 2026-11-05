"""Tests the type annotations on the effect argument of the doeff-traverse handlers.

At install time the VM reads the type annotation of a handler's effect argument (doeff_vm._effect_types, SPEC-WITHHANDLER-TYPE-FILTER), and for effects outside that type
passes the effect outward without entering the handler in Python. The traverse handlers only pass effects outside their handled types outward with a final `Pass`, so adding the annotation
leaves the answers and order unchanged and only reduces the number of times the body is entered.
"""

import sys
from collections.abc import Callable
from types import CodeType, FrameType

import pytest
from doeff_core_effects import Ask
from doeff_core_effects import reader
from doeff_core_effects import scheduled
from doeff_traverse import Fail, Inspect, ItemOutcome, Reduce, Skip, SortBy, Take, Traverse, Zip
from doeff_traverse import fail_handler, normalize_to_none, parallel, parallel_fail_fast, sequential
from doeff import handler_effect_types

from doeff import do, run, with_handlers

# ItemOutcome: when none of the inner handlers receives the item-outcome notice (emitted by the handler itself), the handler itself answers None.
COLLECTION = (Skip, Traverse, Reduce, Zip, Inspect, SortBy, Take, ItemOutcome)


def _raw(installed: object) -> Callable[..., object]:
    """The handler function the VM calls, wrapped by the value of a Program -> Program handler (the field doeff.program.handler sets)."""
    return installed.__doeff_handler_data__  # type: ignore[attr-defined] — a field set by doeff.program.handler (an untyped field)


def _entries(code: CodeType, run_it: Callable[[], object]) -> tuple[int, object]:
    """The number of times the code function started during run_it, and its answer. The body is a generator, so a call event also fires on each resume — count by frame identity."""
    frames: dict[int, FrameType] = {}

    def profile(frame: FrameType, event: str, _arg: object) -> None:
        if event == "call" and frame.f_code is code:
            frames.setdefault(id(frame), frame)

    sys.setprofile(profile)
    try:
        result = run_it()
    finally:
        sys.setprofile(None)
    return len(frames), result


@pytest.mark.parametrize(
    ("installed", "types"),
    [
        (sequential, COLLECTION),
        (parallel(concurrency=2), COLLECTION),
        (parallel_fail_fast(concurrency=2), COLLECTION),
        (fail_handler, (Fail,)),
        (normalize_to_none, (Fail,)),
    ],
)
def test_each_handler_declares_the_types_it_answers(installed: object, types: tuple[type, ...]) -> None:
    assert handler_effect_types(_raw(installed)) == types


@do
def _double(x: int):
    return x * 2


@do
def _asks_then_traverse():
    a = yield Ask("a")
    b = yield Ask("b")
    collection = yield Traverse(_double, [a, b])
    return (yield Inspect(collection))


def test_an_effect_outside_the_types_does_not_enter_the_handler() -> None:
    # The traverse handler is the innermost: Ask is skipped and reaches the outer reader
    handler = sequential
    body = _raw(handler).__wrapped__.__code__  # type: ignore[attr-defined] — a field set by @do's functools.wraps
    entered, result = _entries(
        body, lambda: run(scheduled(with_handlers([reader({"a": 1, "b": 2}), handler], _asks_then_traverse())))
    )
    assert [item.value for item in result] == [2, 4]
    assert entered == 2, "the traverse handler body is entered only twice, for Traverse and Inspect (the 2 Asks are skipped)"
