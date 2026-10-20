"""A handler is a @do function marked with @handler (ADR-DOE-CORE-003).

``handler`` accepts only a @do function (or an installer that already carries the
handler marker), and ``with_handlers`` accepts only installers made that way. Anything
else is refused with a TypeError where the handler or the stack is built, before the
program runs: there is no fallback that turns a plain function into a handler.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

import pytest

from doeff import EffectBase, Resume, do, handler, run, with_handlers


@dataclass(frozen=True)
class Ping(EffectBase):
    """The one effect these tests handle."""


@do
def asks() -> object:
    """A program that yields Ping once and returns the answer."""
    return (yield Ping())


@do
def answer_pong(effect: Ping, k):
    """A @do function that answers Ping, before @handler makes it a handler."""
    return (yield Resume(k, "pong"))


def plain_answer(effect: Ping, k):
    """A plain function shaped like a handler, without @do."""
    return Resume(k, "pong")


class CallableAnswer:
    """A callable instance shaped like a handler, without @do."""

    def __call__(self, effect: Ping, k):
        return Resume(k, "pong")


def test_handler_installs_a_do_function() -> None:
    assert run(handler(answer_pong)(asks())) == "pong"


def test_decorated_handler_installs_itself() -> None:
    @handler
    @do
    def pong(effect: Ping, k):
        return (yield Resume(k, "pong"))

    assert run(pong(asks())) == "pong"


@pytest.mark.parametrize(
    "not_do",
    [
        plain_answer,
        lambda _effect, k: Resume(k, "pong"),
        functools.partial(plain_answer),
        CallableAnswer(),
    ],
    ids=["plain function", "lambda", "partial", "callable instance"],
)
def test_handler_refuses_what_is_not_a_do_function(not_do) -> None:
    with pytest.raises(TypeError, match="not a @do function"):
        handler(not_do)


def test_handler_decorator_refuses_a_plain_function() -> None:
    with pytest.raises(TypeError, match="not a @do function"):

        @handler
        def pong(effect: Ping, k):
            return Resume(k, "pong")


def test_with_handlers_installs_handlers() -> None:
    assert run(with_handlers([handler(answer_pong)], asks())) == "pong"


@pytest.mark.parametrize(
    "not_a_handler",
    [plain_answer, answer_pong, lambda _effect, k: Resume(k, "pong")],
    ids=["plain function", "@do function without @handler", "lambda"],
)
def test_with_handlers_refuses_what_is_not_a_handler(not_a_handler) -> None:
    with pytest.raises(TypeError, match="not a handler"):
        with_handlers([not_a_handler], asks())
