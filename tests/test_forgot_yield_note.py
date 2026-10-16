"""A TypeError from using an effect, a program or a Try outcome where its result was meant
gets a note that says to yield it, and names the type as the user knows it.

Before: ``count = Get("counter")`` then ``count + 1`` ended with ``TypeError: unsupported
operand type(s) for +: 'Get' and 'int'``, ``x = child()`` then ``x + 1`` with ``'doeff_vm.
doeff_vm.Call' and 'int'``, and ``r = yield Try(child())`` then ``r + 1`` with ``'doeff_vm.
doeff_vm.Ok' and 'int'`` — none said that the value was not yielded, and two named an internal
module (first-errors report 2026-10-17, rows 10-12; card ki-1c49d3ce4554).
"""

from __future__ import annotations

import math

import pytest
from doeff_core_effects import Get, Put, Try
from doeff_core_effects.handlers import state, try_handler

from doeff import Ok, do, run


@do
def child():
    return 20


@do
def child_with(n):
    return n


@do
def failing_child():
    raise ValueError("boom")


@do
def uses_an_effect_it_did_not_yield():
    yield Put("counter", 0)
    count = Get("counter")
    return count + 1


@do
def uses_a_program_it_did_not_yield():
    x = child()
    return x + 1


@do
def uses_a_program_with_arguments_it_did_not_yield():
    x = child_with(3)
    return x + 1


@do
def uses_the_ok_of_try_as_the_value():
    r = yield Try(child())
    return r + 1


@do
def uses_the_err_of_try_as_the_value():
    r = yield Try(failing_child())
    return r + 1


@do
def holds_a_yielded_effect_and_uses_one_it_did_not_yield():
    yield Put("counter", 0)
    asked = Get("counter")
    value = yield asked
    count = Get("counter")
    return value + count


def add_one(n):
    return n + 1


@do
def passes_an_effect_it_did_not_yield_to_a_function():
    yield Put("counter", 0)
    count = Get("counter")
    return add_one(count)


@do
def passes_an_effect_it_did_not_yield_to_a_builtin():
    yield Put("counter", 4)
    count = Get("counter")
    return math.sqrt(count)


@do
def adds_a_string_to_an_int():
    yield Put("counter", 0)
    return "a" + 1


@do
def misuses_a_field_of_an_effect_it_holds():
    asked = Get("counter")
    return asked.key + 1


def _type_error_of(program) -> TypeError:
    with pytest.raises(TypeError) as info:
        run(program)
    return info.value


def _notes(error: BaseException) -> list[str]:
    return list(getattr(error, "__notes__", []))


def test_an_effect_used_before_yielding_it_gets_a_note_to_yield_it() -> None:
    error = _type_error_of(state()(uses_an_effect_it_did_not_yield()))
    assert str(error) == "unsupported operand type(s) for +: 'Get' and 'int'"
    assert _notes(error) == [
        "count is the effect Get('counter'), not its result. "
        "Yield the effect to get its result: count = yield Get('counter')"
    ]


def test_a_program_used_before_yielding_it_gets_a_note_and_names_call_without_its_module() -> (
    None
):
    error = _type_error_of(uses_a_program_it_did_not_yield())
    assert str(error) == "unsupported operand type(s) for +: 'Call' and 'int'"
    assert _notes(error) == [
        "x is the program child() builds, not its result. "
        "Yield the program to run it and get its result: x = yield child()"
    ]


def test_a_program_with_arguments_shows_the_call_with_dots() -> None:
    error = _type_error_of(uses_a_program_with_arguments_it_did_not_yield())
    assert _notes(error) == [
        "x is the program child_with(...) builds, not its result. "
        "Yield the program to run it and get its result: x = yield child_with(...)"
    ]


def test_the_ok_of_try_used_as_the_value_gets_a_note_and_names_ok_without_its_module() -> None:
    error = _type_error_of(try_handler(uses_the_ok_of_try_as_the_value()))
    assert str(error) == "unsupported operand type(s) for +: 'Ok' and 'int'"
    assert _notes(error) == [
        "r is Ok(20), a Result of Try (Ok or Err), not the value in it. "
        "Take the value out with match r: case Ok(value) / case Err(error), "
        "or yield the program without Try to get the value itself."
    ]


def test_the_err_of_try_used_as_the_value_gets_the_same_note() -> None:
    error = _type_error_of(try_handler(uses_the_err_of_try_as_the_value()))
    assert str(error) == "unsupported operand type(s) for +: 'Err' and 'int'"
    assert _notes(error) == [
        "r is Err(ValueError('boom')), a Result of Try (Ok or Err), not the value in it. "
        "Take the value out with match r: case Ok(value) / case Err(error), "
        "or yield the program without Try to get the value itself."
    ]


def test_the_note_names_the_variable_of_the_failing_expression_not_another_of_the_same_type(
) -> None:
    error = _type_error_of(state()(holds_a_yielded_effect_and_uses_one_it_did_not_yield()))
    assert _notes(error) == [
        "count is the effect Get('counter'), not its result. "
        "Yield the effect to get its result: count = yield Get('counter')"
    ]


def test_the_note_follows_the_value_into_a_plain_function() -> None:
    error = _type_error_of(state()(passes_an_effect_it_did_not_yield_to_a_function()))
    assert _notes(error) == [
        "n is the effect Get('counter'), not its result. "
        "Yield the effect to get its result: n = yield Get('counter')"
    ]


def test_the_note_comes_for_a_type_error_a_builtin_raises() -> None:
    error = _type_error_of(state()(passes_an_effect_it_did_not_yield_to_a_builtin()))
    assert _notes(error) == [
        "count is the effect Get('counter'), not its result. "
        "Yield the effect to get its result: count = yield Get('counter')"
    ]


def test_a_type_error_without_a_doeff_value_gets_no_note() -> None:
    error = _type_error_of(state()(adds_a_string_to_an_int()))
    assert _notes(error) == []


def test_a_type_error_that_does_not_name_the_effect_gets_no_note() -> None:
    error = _type_error_of(misuses_a_field_of_an_effect_it_holds())
    assert _notes(error) == []


def test_the_doeff_traceback_ends_with_the_note(capsys: pytest.CaptureFixture[str]) -> None:
    _type_error_of(uses_a_program_it_did_not_yield())
    printed = capsys.readouterr().err
    assert printed.rstrip().endswith(
        "TypeError: unsupported operand type(s) for +: 'Call' and 'int'\n"
        "x is the program child() builds, not its result. "
        "Yield the program to run it and get its result: x = yield child()"
    )


def test_running_the_failing_program_twice_does_not_repeat_the_note() -> None:
    @do
    def outer():
        return run(uses_a_program_it_did_not_yield())

    error = _type_error_of(outer())
    assert len(_notes(error)) == 1


def test_ok_keeps_the_module_path_pickle_reads() -> None:
    import pickle

    assert pickle.loads(pickle.dumps(Ok(3))).value == 3
    assert (Ok.__module__, Ok.__qualname__) == ("doeff_vm.doeff_vm", "Ok")
