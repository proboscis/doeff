"""A generator ``@do`` that finishes without emitting an effect has its definition call run in place by the VM inside the parent's stream.

When a parent generator yields a definition ``Call`` whose body yields, the VM's stream creates the child generator in place and drives it
with ``PyIter_Send``, then sends the returned value back to the parent (without using a VM step). If the child yields another ``Call``, it is pushed on the explicit
stack. If any child yields something other than ``Call`` (an effect, etc.) or raises, the chain of generators it started driving is handed to the VM
as "a stream holding the first answer" -- the frame order (parent -> child -> grandchild) and the doeff traceback on exceptions are
the same as when the VM drove everything. The ``open_bind`` (``<-``) and ``Call`` shapes are unchanged (ADR-DOE-CORE-EFFECTS-003 R5).
"""

import itertools
from collections.abc import Generator
from dataclasses import dataclass

import pytest
from doeff_vm import K, PyVM

from doeff import EffectBase, Resume, do, handler, run, with_handlers


class Probe(EffectBase[int]):
    pass


class BoomError(Exception):
    pass


@do
def count_down(n: int) -> Generator[object, int, int]:
    if n == 0:
        return 0
    rest = yield count_down(n - 1)
    return rest + 1


@do
def raises_at(n: int) -> Generator[object, int, int]:
    if n == 0:
        raise BoomError("bottom")
    return (yield raises_at(n - 1))


@handler
def answer_probe(effect: Probe, k: K) -> Generator[object, object, object]:
    return (yield Resume(k, 100))


@dataclass(frozen=True)
class CountedRun:
    steps: int
    value: object


def _counted_run(program: object) -> CountedRun:
    vm = PyVM()
    value = vm.run(program)
    return CountedRun(steps=vm.step_count(), value=value)


def _frame_names(error: BaseException) -> list[str]:
    # The doeff traceback the VM puts on the exception (a list of ``["frame", qualified name, file, line]``) -- an untyped attribute, so read it from __dict__.
    # The name is the last component of the qualified name (a function inside a test is ``<test name>.<locals>.parent``).
    return [
        entry[1].rsplit(".", 1)[-1]
        for entry in vars(error)["__doeff_traceback__"]
        if entry[0] == "frame"
    ]


def test_a_recursion_that_performs_no_effect_takes_the_same_vm_steps_at_any_depth() -> None:
    shallow = _counted_run(count_down(10))
    deep = _counted_run(count_down(20))
    assert (shallow.value, deep.value) == (10, 20)
    assert deep.steps == shallow.steps


def test_a_deep_recursion_runs_past_the_in_place_chain_limit() -> None:
    assert run(count_down(5000)) == 5000


def test_an_effect_at_the_bottom_is_answered_and_each_level_runs_once() -> None:
    entries = itertools.count()

    @do
    def down(n: int) -> Generator[object, int, int]:
        next(entries)  # number of levels entered -- increases if the VM drives a generator it started in place a second time
        if n == 0:
            return (yield Probe())
        rest = yield down(n - 1)
        return rest + 1

    assert run(with_handlers([answer_probe], down(5))) == 105
    assert next(entries) == 6


def test_an_exception_of_an_in_place_callee_is_caught_where_the_caller_yields() -> None:
    @do
    def parent() -> Generator[object, object, object]:
        try:
            yield raises_at(3)
        except BoomError as error:
            return ("caught", str(error))
        return "not caught"

    assert run(parent()) == ("caught", "bottom")


def test_an_uncaught_exception_of_an_in_place_callee_keeps_every_frame() -> None:
    @do
    def parent() -> Generator[object, object, object]:
        return (yield raises_at(2))

    with pytest.raises(BoomError) as info:
        run(parent())
    # Same sequence as when the VM drove everything (after #2801) -- consecutive frames of the same function are folded by the VM
    assert _frame_names(info.value) == ["parent", "raises_at", "raises_at"]


def test_an_exception_after_an_effect_keeps_the_frames_in_call_order() -> None:
    @do
    def grandchild() -> Generator[object, object, None]:
        yield Probe()
        raise BoomError("late")

    @do
    def child() -> Generator[object, object, object]:
        return (yield grandchild())

    @do
    def parent() -> Generator[object, object, object]:
        return (yield child())

    with pytest.raises(BoomError) as info:
        run(with_handlers([answer_probe], parent()))
    assert _frame_names(info.value) == ["parent", "child", "grandchild"]
