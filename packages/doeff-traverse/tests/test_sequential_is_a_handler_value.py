"""sequential is a handler value, not a factory called with no arguments (ADR-DOE-API-002).

The owner's decision of 2026-10-29: a handler without settings is not a
factory. ``sequential`` is applied to the program itself and put in a stack as it is; the former
call ``sequential()`` answers the same handler with a DeprecationWarning for one release.

former-factory-call-rewrite: keep — the former call is written on purpose here.
"""

import warnings

import pytest
from doeff_core_effects import try_handler
from doeff_traverse import Collection, Reduce, Traverse, sequential

from doeff import Pure, do, run, with_handlers


@do
def _doubled(values: list[int]):
    """Traverse doubling each value, then Reduce summing them — the two branches that reinstall
    sequential around the program of an item."""
    collection = yield Traverse(lambda v: Pure(v * 2), values)
    assert isinstance(collection, Collection)
    return (yield Reduce(lambda acc, v: Pure(acc + v), 0, collection))


def test_sequential_is_a_marked_handler_value():
    """It carries the handler marker without being called, so it goes in a stack as it is."""
    assert vars(sequential)["_doeff_is_handler_fn"] is True
    assert sequential.__name__ == "sequential"


def test_sequential_answers_applied_to_the_program():
    """``sequential(prog)`` answers the Traverse and the Reduce."""
    assert run(sequential(_doubled([1, 2, 3]))) == 12


def test_sequential_goes_in_a_stack_without_a_call():
    """``[try_handler, sequential]`` — the stack takes the value as it is."""
    assert run(with_handlers([try_handler, sequential], _doubled([4, 5]))) == 18


def test_the_former_call_warns_and_answers_the_same_handler():
    """``sequential()`` keeps running for one release: a DeprecationWarning, and the handler itself."""
    with pytest.warns(DeprecationWarning, match="sequential has no settings"):
        called = sequential()
    assert called is sequential
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert run(sequential()(_doubled([1]))) == 2
