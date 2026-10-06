"""The Redis handler's calls are made only when they are awaited (agora-redesign #3864 — the warning
"coroutine '_or_failed' was never awaited" seen on the real server).

Every operation of ``redis_notice_handler`` is one ``Await``. When the task that performs it is stopped after the
effect was made but before the answerer of ``Await`` took it, the effect is dropped. If the call's coroutine was
already made, it is dropped without being awaited and Python warns. The handler therefore hands ``Await`` an
awaitable that makes the call's coroutine only when it is awaited: dropping the effect leaves nothing behind.

The layer below stands in for "stopped before the answerer took it": it answers every ``Await`` by raising into
the body without awaiting it. No server is needed — nothing is ever awaited.

No ``from __future__ import annotations`` here: the VM reads a handler's effect types from the annotation of its
first parameter.
"""

import gc
import warnings
from typing import TYPE_CHECKING

import pytest
from doeff_core_effects.effects import Await
from doeff_core_effects.scheduler import scheduled
from doeff_events.effects.notices import Announce, ProbeBroker
from doeff_events.handlers.redis_notices import redis_notice_handler

from doeff import K, ResumeThrow, do, run
from doeff import handler as program_handler

if TYPE_CHECKING:
    from doeff import EffectGenerator
    from doeff.program import ProgramHandler


class _DroppedError(RuntimeError):
    """What the dropping layer raises into the body instead of awaiting."""


def _drops_awaits() -> "ProgramHandler":
    """The layer outside the Redis handler that never awaits: it answers ``Await`` by raising into the body."""

    @do
    def handler(effect: Await, k: K) -> "EffectGenerator[object]":
        """Drop the awaitable without awaiting it."""
        return (yield ResumeThrow(k, _DroppedError("the Await was dropped before it was awaited")))

    return program_handler(handler)


@do
def _sends_one() -> "EffectGenerator[object]":
    """Ask the broker to take one notice (the Await of the send is dropped)."""
    return (yield Announce("law:dropped", "law-note", "never sent"))


@do
def _tries_one() -> "EffectGenerator[object]":
    """Try the connection once (the Await of the try is dropped)."""
    return (yield ProbeBroker())


@pytest.mark.parametrize("body", [_sends_one, _tries_one], ids=["announce", "probe"])
def test_a_call_whose_await_is_dropped_leaves_no_unawaited_coroutine(body) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        with pytest.raises(_DroppedError):
            run(scheduled(_drops_awaits()(redis_notice_handler("redis://127.0.0.1:9/0", 1.0)(body()))))
        # A dropped coroutine warns when it is collected; collecting here makes the warning an error inside this
        # block instead of a stray line later.
        gc.collect()
