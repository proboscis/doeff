"""A handler is a def marked with @handler, which makes it a @do function (ADR-DOE-CORE-003).

``@handler`` takes the def as written and makes it a ``@do`` function, so ``@do`` is not
written under it (``@handler`` over ``@do`` is deprecated and still runs). A ``@do`` function
is taken as it is. A lambda and a callable instance are refused where the handler is made,
and ``with_handlers`` takes only installers: there is no fallback that turns an unmarked
function into a handler on the way into a stack. A body without ``yield`` that returns a
control node unrun (``return Resume(k, v)``) is refused when it runs, with the form to write.
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


def answer_def(effect: Ping, k):
    """A def that answers Ping, before @handler makes it a handler."""
    return (yield Resume(k, "pong"))


@do
def answer_pong(effect: Ping, k):
    """A @do function that answers Ping, also used as a Kleisli elsewhere."""
    return (yield Resume(k, "pong"))


def unrun_answer(effect: Ping, k):
    """A def without yield that returns its Resume instead of yielding it."""
    return Resume(k, "pong")


class CallableAnswer:
    """A callable instance shaped like a handler."""

    def __call__(self, effect: Ping, k):
        return Resume(k, "pong")


def test_handler_alone_makes_the_def_a_do_function() -> None:
    @handler
    def pong(effect: Ping, k):
        return (yield Resume(k, "pong"))

    assert run(pong(asks())) == "pong"
    assert run(handler(answer_def)(asks())) == "pong"


def test_handler_takes_a_do_function_as_it_is() -> None:
    assert run(handler(answer_pong)(asks())) == "pong"
    assert run(handler(functools.partial(answer_pong))(asks())) == "pong"


def test_handler_over_do_still_runs() -> None:
    @handler
    @do
    def pong(effect: Ping, k):
        return (yield Resume(k, "pong"))

    assert run(pong(asks())) == "pong"


def test_a_body_without_yield_ends_the_block_with_its_value() -> None:
    @handler
    def refuse(effect: Ping, k):
        return "refused"

    assert run(refuse(asks())) == "refused"


def test_a_body_returning_its_resume_unrun_is_refused_with_the_form_to_write() -> None:
    with pytest.raises(TypeError, match=r"returned Resume\(\.\.\.\) without yielding it.*return \(yield Resume"):
        run(handler(unrun_answer)(asks()))


@pytest.mark.parametrize(
    "not_a_def",
    [lambda _effect, k: Resume(k, "pong"), CallableAnswer(), functools.partial(unrun_answer), print],
    ids=["lambda", "callable instance", "partial of a plain function", "builtin"],
)
def test_handler_refuses_what_is_not_a_def(not_a_def) -> None:
    with pytest.raises(TypeError, match="is not a handler body"):
        handler(not_a_def)


def test_with_handlers_installs_handlers() -> None:
    assert run(with_handlers([handler(answer_def)], asks())) == "pong"


@pytest.mark.parametrize(
    "not_a_handler",
    [answer_def, answer_pong, lambda _effect, k: Resume(k, "pong")],
    ids=["def without @handler", "@do function without @handler", "lambda"],
)
def test_with_handlers_refuses_what_is_not_a_handler(not_a_handler) -> None:
    with pytest.raises(TypeError, match="not a handler"):
        with_handlers([not_a_handler], asks())
