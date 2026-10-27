"""A handler attached the wrong way gets a TypeError, where it was attached, that names the
right way to attach it.

Before (first-look report 2026-10-17, table rows 4, 5 and 8 — the same in 0.5.0 and main):

- ``state(prog())`` — a handler factory given the program — failed inside the factory with
  ``ValueError: dictionary update sequence element #0 has length 1; 2 is required``;
- ``writer()(prog())`` — a handler called as if it were a factory — with
  ``TypeError: writer() missing 1 required positional argument: 'body'``;
- ``fetch_handler(prog())`` — an ``(effect, k)`` handler body under ``@do`` instead of ``@handler`` — with
  ``TypeError: fetch_handler() missing 1 required positional argument: 'k'``.

None of them said how to attach the handler (card ki-8152466e96ad's list).
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass

import pytest
from doeff_core_effects import Ask, Get, Put, ReadWriterLog, Tell
from doeff_core_effects.handlers import (
    lazy_ask,
    listen_handler,
    reader,
    state,
    try_handler,
    writer,
)
from doeff_core_effects.scheduler import scheduled

from doeff import EffectBase, Resume, do, handler, run


@do
def counter():
    yield Put("n", 1)
    return (yield Get("n"))


@do
def add(x, y):
    yield Put("n", x + y)
    return (yield Get("n"))


@do
def asks_key():
    return (yield Ask("key"))


@do
def tells_hello():
    yield Tell("hello")
    return (yield ReadWriterLog())


@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str


@do
def fetch_handler(effect: Fetch, k):
    return (yield Resume(k, f"got {effect.url}"))


@do
def unannotated_handler(effect, k):
    return (yield Resume(k, f"got {effect.url}"))


@do
def fetches():
    return (yield Fetch("https://example.com"))


@do
def retry(program, times):
    return (yield program)


def _type_error(attach) -> TypeError:
    with pytest.raises(TypeError) as info:
        run(attach())
    return info.value


# --- 1. a handler factory given the program: state(prog) ---


def test_state_given_the_program_says_to_call_the_factory_first() -> None:
    message = str(_type_error(lambda: state(counter())))
    assert message == (
        "state() got a program, not the initial state: state is a handler factory. "
        "Call it to build the handler, then apply that to the program: state()(counter())"
    )


def test_reader_and_lazy_ask_given_the_program_say_the_same() -> None:
    assert str(_type_error(lambda: reader(asks_key()))).endswith(": reader()(asks_key())")
    assert str(_type_error(lambda: lazy_ask(asks_key()))).endswith(": lazy_ask()(asks_key())")
    assert "not the env" in str(_type_error(lambda: reader(asks_key())))


def test_a_program_with_arguments_is_shown_with_dots() -> None:
    assert str(_type_error(lambda: state(add(1, 2)))).endswith(": state()(add(...))")


def test_a_factory_given_the_uncalled_do_function_says_to_call_both() -> None:
    message = str(_type_error(lambda: state(counter)))
    assert message.startswith("state() got the @do function 'counter', not the initial state")
    assert message.endswith(": state()(counter())")


def test_the_factory_is_refused_where_it_is_called() -> None:
    with pytest.raises(TypeError) as info:
        state(counter())
    assert traceback.extract_tb(info.value.__traceback__)[0].line == "state(counter())"


def test_factories_still_take_their_values() -> None:
    assert run(state()(counter())) == 1
    assert run(state({"n": 0})(counter())) == 1
    assert run(reader({"key": "v"})(asks_key())) == "v"
    assert run(scheduled(lazy_ask({"key": "v"})(asks_key()))) == "v"


# --- 2. a handler called as if it were a factory: writer()(prog) ---


def test_writer_called_with_no_program_says_to_apply_it_to_the_program() -> None:
    message = str(_type_error(lambda: state()(writer()(tells_hello()))))
    assert message == (
        "writer() got no program: writer is a handler, not a handler factory. "
        "Apply it to the program itself: writer(program), not writer()(program)"
    )


def test_every_ready_handler_says_the_same() -> None:
    for ready, name in ((try_handler, "try_handler"), (listen_handler, "listen_handler")):
        with pytest.raises(TypeError) as info:
            ready()
        assert str(info.value).endswith(f": {name}(program), not {name}()(program)")


def test_a_handler_still_takes_the_program() -> None:
    assert run(state()(writer(tells_hello()))) == ["hello"]


# --- 3. an (effect, k) handler body under @do instead of @handler: fetch_handler(prog) ---


def test_an_effect_handler_called_with_the_program_says_to_mark_it_with_handler() -> None:
    message = str(_type_error(lambda: fetch_handler(fetches())))
    assert message == (
        "fetch_handler takes (effect, k): it is a handler body, not a function that builds a "
        "program, so fetch_handler(fetches()) cannot run. Mark its def with @handler in place "
        "of @do: then fetch_handler(fetches()) runs the program under it"
    )


def test_an_unannotated_effect_handler_says_the_same() -> None:
    message = str(_type_error(lambda: unannotated_handler(fetches())))
    assert message.endswith(": then unannotated_handler(fetches()) runs the program under it")


def test_a_handler_given_the_uncalled_do_function_says_to_call_it_too() -> None:
    message = str(_type_error(lambda: fetch_handler(fetches)))
    assert message.endswith(": then fetch_handler(fetches()) runs the program under it")


def test_the_same_error_at_a_yield_of_the_misapplied_handler() -> None:
    @do
    def body():
        try:
            yield fetch_handler(fetches())
        except TypeError as exc:
            return f"caught: {exc}"
        return "not raised"

    assert run(body()).endswith(": then fetch_handler(fetches()) runs the program under it")


def test_a_function_that_is_not_a_handler_keeps_the_missing_argument_error() -> None:
    message = str(_type_error(lambda: retry(counter())))
    assert message == "retry() missing 1 required positional argument: 'times'"


def test_a_handler_called_with_an_effect_keeps_the_missing_argument_error() -> None:
    message = str(_type_error(lambda: fetch_handler(Fetch("https://example.com"))))
    assert message == "fetch_handler() missing 1 required positional argument: 'k'"


def test_the_handler_body_marked_with_handler_runs() -> None:
    @handler
    def marked(effect: Fetch, k):
        return (yield Resume(k, f"got {effect.url}"))

    assert run(marked(fetches())) == "got https://example.com"
    # handler() of the @do function is the same handler.
    assert run(handler(fetch_handler)(fetches())) == "got https://example.com"
