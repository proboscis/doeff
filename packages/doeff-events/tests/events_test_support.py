"""Shared run helper for the doeff-events test suite.

``event_handler`` blocks listeners on scheduler promises (CreatePromise / Wait)
and the tests delay publishers with ``Await``, so programs run under the
scheduler with ``await_handler`` inside it. This replaces the removed
``run(program, handlers=default_handlers())`` surface by composing the
handlers explicitly. Uniquely named (not ``conftest``) so it does not collide
with other suites' helper modules when collected together.
"""

from typing import TYPE_CHECKING, Any, final

from doeff_core_effects import await_handler
from doeff_core_effects import CompletePromise, Promise, scheduled
from doeff_events import BrokerPatience, NextFileChanges, WaitForEventEffect, WaitForEventsEffect
from doeff_events import broker_patience_handler, notice_events_handler

from doeff import K, Pass, do, run
from doeff import handler as program_handler

if TYPE_CHECKING:
    from collections.abc import Callable

    from doeff_events import NoticeRoute

    from doeff import EffectGenerator, Program
    from doeff.program import ProgramHandler


def patient_notice_events(
    source: str, routes: "tuple[NoticeRoute[object], ...]", patience_seconds: float
) -> "Callable[[Program[object]], Program[object]]":
    """``notice_events_handler(source, routes)`` with the composition's answer to its ``ReadBrokerPatience``
    (``patience_seconds``) placed right outside it — what a test's composition places once for its wrappers."""
    events = notice_events_handler(source, routes)
    patience = broker_patience_handler(BrokerPatience(patience_seconds))

    def wrapped(body: "Program[object]") -> "Program[object]":
        return patience(events(body))

    return wrapped


def run_scheduled(program: Any) -> Any:
    """Run ``program`` under the scheduler with ``Await`` support and return its value."""

    # await_handler performs scheduler effects, so it sits inside ``scheduled``.
    return run(scheduled(await_handler(program)))


def announce_listening(registered: Promise[object]) -> "ProgramHandler":
    """Handler that completes ``registered`` when the program under it starts to listen with ``WaitForEvent`` or
    ``WaitForEvents``.

    ``event_handler()`` drops a ``Publish`` that comes while nobody listens, so a publisher has to publish after the
    listener is registered. The listener's task runs ``announce_listening(registered)(listener())`` and the publisher
    waits for ``registered.future``: the order comes from that event, not from how long the publisher waits or from
    which task is spawned first. The handler sits inside ``event_handler()`` (or ``subscribed_event_handler``) and
    passes the effect on to it. ``subscribed_event_handler`` keeps a ``Publish`` that comes before the wait, so there
    the event decides whether a ``Publish`` wakes a parked listener or only finds its queue.

    It completes with ``yield_to_woken=False``: the listener keeps its turn through ``event_handler``'s registration
    (``CreatePromise`` and the listener table) and parks on its own ``Wait``; only then does the publisher run. With the
    default ``True`` the publisher runs first and publishes into an empty table. It completes ``registered`` once, so
    the program under it listens once.
    """

    @do
    def handler(
        effect: WaitForEventEffect | WaitForEventsEffect, k: K
    ) -> "EffectGenerator[object]":
        yield CompletePromise(registered, None, yield_to_woken=False)
        yield Pass(effect, k)

    return program_handler(handler)


def announce_file_wait(registered: Promise[object]) -> "ProgramHandler":
    """Handler that completes ``registered`` when the program under it starts to wait with ``NextFileChanges``.

    The file-watch answerer's wait (``memory_file_watch_handler``) records its waiter as it parks, so a task that must
    act on a parked waiter (close the watch under it, announce a change to it) waits for ``registered.future``: the
    order comes from that event, not from how long the task waits. The handler sits inside the answerer and passes the
    effect on to it. It completes with ``yield_to_woken=False`` for the reason ``announce_listening`` gives, and once.
    """

    @do
    def handler(effect: NextFileChanges, k: K) -> "EffectGenerator[object]":
        yield CompletePromise(registered, None, yield_to_woken=False)
        yield Pass(effect, k)

    return program_handler(handler)


@final
class Wakes:
    """The promises ``record_wakes`` saw completed, in the order they were completed."""

    __slots__ = ("mut_promises",)

    def __init__(self) -> None:
        """Start with nothing seen."""
        self.mut_promises: tuple[Promise[object], ...] = ()


def record_wakes(wakes: Wakes) -> "ProgramHandler":
    """Handler that records each promise the program under it completes in ``wakes``, then lets the completion go on.

    ``subscribed_event_handler`` keeps a ``Publish`` that comes before the wait in the listener's queue, so the answer
    does not tell a listener woken by a ``Publish`` from a signal found in the queue. A ``Publish`` that wakes a parked
    listener completes that listener's promise from the publisher's handler; this handler sits outside the publisher's
    handler, and ``wakes`` then shows whether the ``Publish`` woke a parked listener.
    """

    @do
    def handler(effect: CompletePromise, k: K) -> "EffectGenerator[object]":
        wakes.mut_promises = (*wakes.mut_promises, effect.promise)
        yield Pass(effect, k)

    return program_handler(handler)
