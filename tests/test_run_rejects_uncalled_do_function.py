"""run() given a @do function itself (not called) says to call it first.

Before: ``run(main)`` failed with ``TypeError: expected DoExpr or EffectBase, got:
<class 'function'>`` — the message names neither the function nor the fix
(first-look report 2026-10-16, card doeff-publish 6 / 2).
"""

from __future__ import annotations

import pytest

from doeff import do, run


@do
def answer():
    return 42


@do
def add(x, y):
    return x + y


@do
def greet(name="world"):
    return f"hello {name}"


def test_run_names_the_call_when_given_an_uncalled_do_function() -> None:
    with pytest.raises(TypeError) as info:
        run(answer)
    message = str(info.value)
    assert "@do function 'answer' itself, not a program" in message
    assert "run(answer())" in message


def test_run_shows_arguments_are_needed_when_the_do_function_has_required_parameters() -> None:
    with pytest.raises(TypeError, match=r"run\(add\(\.\.\.\)\)"):
        run(add)


def test_run_shows_an_empty_call_when_every_parameter_has_a_default() -> None:
    with pytest.raises(TypeError, match=r"run\(greet\(\)\)"):
        run(greet)


def test_run_error_for_an_uncalled_do_function_carries_no_vm_message() -> None:
    with pytest.raises(TypeError) as info:
        run(answer)
    assert "expected DoExpr" not in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


def test_run_still_runs_the_called_do_function() -> None:
    assert run(answer()) == 42
    assert run(greet("doeff")) == "hello doeff"
