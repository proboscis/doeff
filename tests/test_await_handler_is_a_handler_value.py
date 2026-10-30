"""await_handler is a handler value, not a factory called with no arguments (ADR-DOE-API-002).

The owner's decision of 2026-10-29 (agora card ki-8d7e5eb863c7): a handler without settings is not a
factory. ``await_handler`` is applied to the program itself and put in a stack as it is; the former
call ``await_handler()`` answers the same handler with a DeprecationWarning for one release.
"""

import asyncio
import warnings

import pytest
from doeff_core_effects import await_handler, scheduled, try_handler

from doeff import Await, do, run, with_handlers


@do
def _awaits(value: int):
    """A program that waits for one coroutine answering ``value`` — what await_handler answers."""
    return (yield Await(asyncio.sleep(0, value)))


def test_await_handler_is_a_marked_handler_value():
    """It carries the handler marker without being called, so it goes in a stack as it is."""
    assert vars(await_handler)["_doeff_is_handler_fn"] is True


def test_await_handler_answers_an_await_applied_to_the_program():
    """``await_handler(prog)`` under ``scheduled`` answers the Await."""
    assert run(scheduled(await_handler(_awaits(3)))) == 3


def test_await_handler_goes_in_a_stack_without_a_call():
    """``[await_handler, try_handler]`` — the stack takes the value as it is."""
    assert run(scheduled(with_handlers([await_handler, try_handler], _awaits(4)))) == 4


def test_the_former_call_warns_and_answers_the_same_handler():
    """``await_handler()`` keeps running for one release: a DeprecationWarning, and the handler itself."""
    with pytest.warns(DeprecationWarning, match="await_handler has no settings"):
        called = await_handler()
    assert called is await_handler
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert run(scheduled(await_handler()(_awaits(5)))) == 5
