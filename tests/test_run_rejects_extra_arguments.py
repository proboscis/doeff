"""run() given more than the program says to install handlers by wrapping the program.

Before: ``run(prog(), env={"greeting": "hello"})`` failed with ``TypeError: run() got an
unexpected keyword argument 'env'`` — the message does not say where env goes
(first-errors report 2026-10-17, table row 3, card doeff-publish 8). ``run`` takes
only the program; handlers such as ``reader(env={...})`` are installed by calling
them on it.
"""

from __future__ import annotations

import pytest

from doeff import do, run
from doeff_core_effects import Ask
from doeff_core_effects.handlers import reader


@do
def greet():
    return (yield Ask("greeting"))


@do
def explode():
    raise RuntimeError("the program ran")
    yield Ask("greeting")


def test_run_with_env_says_to_install_reader_around_the_program() -> None:
    with pytest.raises(TypeError) as info:
        run(greet(), env={"greeting": "hello"})
    message = str(info.value)
    assert "env=" in message
    assert "run(reader(env={...})(prog))" in message
    assert "unexpected keyword argument" not in message


def test_run_with_handlers_names_the_argument_and_the_composition() -> None:
    with pytest.raises(TypeError) as info:
        run(greet(), handlers=[reader(env={"greeting": "hello"})])
    message = str(info.value)
    assert "handlers=" in message
    assert "run(reader(env={...})(prog))" in message


def test_run_with_a_second_positional_argument_says_the_same() -> None:
    with pytest.raises(TypeError) as info:
        run(greet(), [reader(env={"greeting": "hello"})])
    message = str(info.value)
    assert "run() takes only the program" in message
    assert "run(reader(env={...})(prog))" in message


def test_run_with_extra_arguments_does_not_start_the_program() -> None:
    with pytest.raises(TypeError, match=r"run\(\) takes only the program"):
        run(explode(), env={"greeting": "hello"})


def test_run_with_the_composition_from_the_message_runs() -> None:
    assert run(reader(env={"greeting": "hello"})(greet())) == "hello"
