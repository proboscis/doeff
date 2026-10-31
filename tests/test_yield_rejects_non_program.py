"""A @do body that yields something other than an effect or a program gets a TypeError
at the yield that says how to fix it.

Before: ``yield 42`` failed with ``RuntimeError: VM error (non-exception value in Raise):
"expected DoExpr or EffectBase, got: <class 'int'>"`` — the VM raised its message string,
not an exception — and ``x = yield sub`` (the @do function, not called) with the same
words for ``<class 'function'>``. Neither named the fix (first-look report 2026-10-16).
"""

from __future__ import annotations

import traceback

import pytest

from doeff import do, run


@do
def answer():
    return 42


@do
def add(x, y):
    return x + y


def plain_generator():
    yield answer()


@do
def yields_a_plain_value():
    x = yield 42
    return x


@do
def yields_the_uncalled_do_function():
    x = yield answer
    return x


@do
def yields_an_uncalled_do_function_with_parameters():
    x = yield add
    return x


@do
def yields_a_generator_without_do():
    x = yield plain_generator()
    return x


@do
def catches_its_bad_yield():
    try:
        yield 42
    except TypeError as exc:
        return f"caught: {exc}"
    return "not raised"


@do
def calls_a_body_that_yields_a_plain_value():
    return (yield yields_a_plain_value())


def _type_error_of(program) -> TypeError:
    with pytest.raises(TypeError) as info:
        run(program)
    return info.value


def test_yielding_a_plain_value_raises_a_type_error_that_says_what_to_yield() -> None:
    message = str(_type_error_of(yields_a_plain_value()))
    assert message.startswith("yield got 42 (int), which is neither an effect nor a program.")
    assert "x = yield sub()" in message
    assert "a plain value needs no yield" in message
    assert "non-exception value" not in message


def test_yielding_an_uncalled_do_function_says_to_call_it() -> None:
    message = str(_type_error_of(yields_the_uncalled_do_function()))
    assert message == (
        "yield got the @do function 'answer' itself, not a program. "
        "Call the function to build the program, then yield that: yield answer()"
    )


def test_the_call_shows_arguments_are_needed_when_the_function_has_required_parameters() -> None:
    message = str(_type_error_of(yields_an_uncalled_do_function_with_parameters()))
    assert message.endswith("yield add(...)")


def test_yielding_a_generator_of_a_function_without_do_says_to_put_do_on_it() -> None:
    message = str(_type_error_of(yields_a_generator_without_do()))
    assert message == (
        "yield got a generator from 'plain_generator', which has no @do. "
        "Put @do on 'plain_generator' so that calling it builds a program."
    )


def test_a_long_value_is_shortened_in_the_message() -> None:
    @do
    def yields_a_long_list():
        yield list(range(10_000))

    message = str(_type_error_of(yields_a_long_list()))
    assert message.startswith("yield got [0, 1, 2, 3, 4, 5, ...] (list)")
    assert len(message) < 400


def test_the_type_error_is_raised_at_the_yield_and_the_body_can_catch_it() -> None:
    result = run(catches_its_bad_yield())
    assert result.startswith("caught: yield got 42 (int)")


def test_the_traceback_ends_at_the_yield_line() -> None:
    error = _type_error_of(yields_a_plain_value())
    last = traceback.extract_tb(error.__traceback__)[-1]
    assert last.name == "yields_a_plain_value"
    assert last.line == "x = yield 42"


def test_a_sub_program_run_in_place_gets_the_same_error_at_its_own_yield() -> None:
    error = _type_error_of(calls_a_body_that_yields_a_plain_value())
    assert str(error).startswith("yield got 42 (int)")
    assert traceback.extract_tb(error.__traceback__)[-1].name == "yields_a_plain_value"


def test_yielding_a_called_do_function_still_runs_it() -> None:
    @do
    def yields_the_called_function():
        x = yield answer()
        y = yield add(x, 1)
        return y

    assert run(yields_the_called_function()) == 43
