"""Shared run helper for the doeff-events test suite.

``event_handler`` blocks listeners on scheduler promises (CreatePromise / Wait)
and the tests delay publishers with ``Await``, so programs run under the
scheduler with ``await_handler`` inside it. This replaces the removed
``run(program, handlers=default_handlers())`` surface by composing the
handlers explicitly. Uniquely named (not ``conftest``) so it does not collide
with other suites' helper modules when collected together.
"""

from typing import TYPE_CHECKING, Any

from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import CompletePromise, Promise, scheduled
from doeff_events.effects import WaitForEventEffect

from doeff import K, Pass, do, run
from doeff import handler as program_handler

if TYPE_CHECKING:
    from doeff import EffectGenerator
    from doeff.program import ProgramHandler


def run_scheduled(program: Any) -> Any:
    """Run ``program`` under the scheduler with ``Await`` support and return its value."""

    # await_handler performs scheduler effects, so it sits inside ``scheduled``.
    return run(scheduled(await_handler()(program)))


def announce_listening(registered: Promise[object]) -> "ProgramHandler":
    """Handler that completes ``registered`` when the program under it starts to listen with ``WaitForEvent``.

    ``event_handler()`` drops a ``Publish`` that comes while nobody listens, so a publisher has to publish after the
    listener is registered. The listener's task runs ``announce_listening(registered)(listener())`` and the publisher
    waits for ``registered.future``: the order comes from that event, not from how long the publisher waits or from
    which task is spawned first. The handler sits inside ``event_handler()`` and passes the effect on to it.

    It completes with ``yield_to_woken=False``: the listener keeps its turn through ``event_handler``'s registration
    (``CreatePromise`` and the listener table) and parks on its own ``Wait``; only then does the publisher run. With the
    default ``True`` the publisher runs first and publishes into an empty table. It completes ``registered`` once, so
    the program under it listens once.
    """

    @do
    def handler(effect: WaitForEventEffect, k: K) -> "EffectGenerator[object]":
        yield CompletePromise(registered, None, yield_to_woken=False)
        yield Pass(effect, k)

    return program_handler(handler)
