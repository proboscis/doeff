"""``@do`` on an ``async def`` function is a TypeError where the decorator is applied, and the
message says how to write the awaits: ``yield Await(...)`` under ``await_handler`` inside
``scheduled()``.

Before: ``@do`` over ``async def prog(): await asyncio.sleep(0.01); return 42`` said nothing,
and ``run(scheduled(prog()))`` returned ``<coroutine object prog at 0x…>`` with exit 0 — the only
sign was Python's ``RuntimeWarning: coroutine 'prog' was never awaited``. An async generator
function (``async def`` with ``yield``) returned ``<async_generator object …>`` the same way
(first-error table row 13, card ki-8152466e96ad).
"""

from __future__ import annotations

import asyncio
import functools
import traceback
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any

import pytest
from doeff_core_effects import await_handler, scheduled

from doeff import Await, do, run


# Written as plain async functions and given to do(...) by a call: the decorator spelling
# (@do over async def) is what the semgrep rule doeff-no-async-in-do-function bans in source;
# these tests pin what @do does at run time when that spelling gets through.
async def fetch_answer() -> int:
    await asyncio.sleep(0)
    return 42


async def answer_with_offset(offset: int) -> int:
    await asyncio.sleep(0)
    return 42 + offset


async def stream_answers() -> AsyncGenerator[int]:
    await asyncio.sleep(0)
    yield 42


COROUTINE_FUNCTION_MESSAGE = (
    "@do cannot wrap 'fetch_answer': it is an async def function, so calling it would "
    "return a coroutine that nothing awaits, not a program. Make it a plain def and write "
    "each await as a yield of Await: value = yield Await(coro) instead of value = await coro "
    "(Await from doeff). Await is handled by await_handler, which needs scheduled outside "
    "it: run(scheduled()(await_handler(fetch_answer()))) (await_handler from "
    "doeff_core_effects._handlers, scheduled from doeff_core_effects._scheduler)."
)

ASYNC_GENERATOR_FUNCTION_MESSAGE = (
    "@do cannot wrap 'stream_answers': it is an async generator function (async def with "
    "yield), so calling it would return an async generator, not a program. Make it a plain "
    "def that keeps its yields and write each await as a yield of Await: value = yield "
    "Await(coro) instead of value = await coro (Await from doeff). Await is handled by "
    "await_handler, which needs scheduled outside it: "
    "run(scheduled()(await_handler(stream_answers()))) (await_handler from "
    "doeff_core_effects._handlers, scheduled from doeff_core_effects._scheduler)."
)


def _decorate(
    decorator: Callable[[Callable[..., Any]], Any],
    fn: Callable[..., Any],
) -> TypeError:
    with pytest.raises(TypeError) as caught:
        decorator(fn)
    return caught.value


def test_do_on_a_coroutine_function_is_a_type_error_that_names_yield_await() -> None:
    error = _decorate(do, fetch_answer)
    assert str(error) == COROUTINE_FUNCTION_MESSAGE


def test_do_on_an_async_generator_function_is_a_type_error_that_names_yield_await() -> None:
    error = _decorate(do, stream_answers)
    assert str(error) == ASYNC_GENERATOR_FUNCTION_MESSAGE


def test_do_with_options_refuses_async_def_the_same_way() -> None:
    assert str(_decorate(do(non_tail=True), fetch_answer)) == COROUTINE_FUNCTION_MESSAGE
    assert str(_decorate(do(non_tail=True), stream_answers)) == ASYNC_GENERATOR_FUNCTION_MESSAGE


def test_the_run_line_shows_the_arguments_when_the_function_needs_them() -> None:
    message = str(_decorate(do, answer_with_offset))
    assert "@do cannot wrap 'answer_with_offset': it is an async def function" in message
    assert "run(scheduled()(await_handler(answer_with_offset(...))))" in message


def test_a_partial_of_a_coroutine_function_is_refused_with_the_wrapped_name() -> None:
    message = str(_decorate(do, functools.partial(answer_with_offset, 1)))
    assert message.startswith("@do cannot wrap 'answer_with_offset': it is an async def function")


def test_the_error_is_raised_where_the_decorator_is_applied() -> None:
    """The traceback ends in the user's code at the line that applies ``do`` — not at a later
    ``run``, which is never reached."""
    with pytest.raises(TypeError) as caught:
        do(fetch_answer)  # the line the traceback must reach

    frames = traceback.extract_tb(caught.value.__traceback__)
    # resolve: the checkout may be reached through a symlink, and the frame and __file__
    # can name the same file by different paths
    this_file = Path(__file__).resolve()
    user_frames = [frame for frame in frames if Path(frame.filename).resolve() == this_file]
    assert user_frames, frames
    assert user_frames[-1].line == "do(fetch_answer)  # the line the traceback must reach"


def test_the_advised_rewrite_runs() -> None:
    """失敗ケースの対: the message's rewrite — a plain def yielding Await, run under
    await_handler inside scheduled() — gives the value the async def meant."""

    @do
    def fetch_answer_program():
        value = yield Await(fetch_answer())
        return value

    assert run(scheduled()(await_handler(fetch_answer_program()))) == 42


def test_plain_and_generator_functions_are_still_accepted() -> None:
    """失敗ケースの対: the refusal reads only the async flags of the code."""

    @do
    def generator_program():
        value = yield Await(fetch_answer())
        return value + 1

    @do
    def plain_program() -> int:
        return 7

    assert run(scheduled()(await_handler(generator_program()))) == 43
    assert run(plain_program()) == 7
