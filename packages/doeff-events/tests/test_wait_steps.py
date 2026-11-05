"""Check of the doeff-vm step count of ``WaitForEvent`` / ``WaitForEvents`` that wait and are then woken, in a handler with per-subscriber queues (``subscribed_event_handler``).

When ``WaitForEvents`` was added (2ae3daeb7), the "take from the queue; if none, wait via a promise" shared by the two waits was made a ``@do`` helper Program.
The helper Program runs one extra level on every wait, so each woken wait grew by 3 steps, and agora's screen check (test_screen_card_accept_replies) grew by
573 steps in one test and exceeded the repo's step limit (daily run 2026-10-07 08:00). The sharing is written in a form that adds no steps (delegating a plain generator with ``yield from``).

The comparison target is ``event_handler``, which has no queues (the same wait written directly inside the handler). The two assemblies differ only by the one step
of wrapping a per-subscriber handler over the waiter's task (``event_handler`` shares one among everyone and wraps it only once on the outside).
"""

from dataclasses import dataclass

import pytest
from doeff_core_effects import Spawn, Wait, scheduled
from doeff_core_effects._scheduler import IMPLEMENTATION_ENV_VAR, _read_vm_steps

from doeff import do, run, with_handlers
from doeff_events import (
    EventBus,
    Publish,
    WaitForEvent,
    WaitForEvents,
    event_handler,
    subscribed_event_handler,
)

# The step of wrapping a per-subscriber handler over the waiter's task (absent from the event_handler assembly).
WAITER_HANDLER_STEPS = 1
# The scheduler version (the scheduled implementation; the value of the environment variable DOEFF_SCHEDULER).
SCHEDULERS = ("python", "rust")


@dataclass(frozen=True)
class Signal:
    """A signal for the check."""

    n: int


def steps_of(program: object) -> int:
    """The number of doeff-vm steps when ``program`` is run under the scheduler."""
    before = _read_vm_steps()
    run(scheduled(program))
    return _read_vm_steps() - before


@do
def waiting(many: bool):
    """Wait for one signal (``WaitForEvents`` if ``many``)."""
    if many:
        got = yield WaitForEvents(Signal)
    else:
        got = yield WaitForEvent(Signal)
    return got


@do
def publishing():
    """Emit one signal."""
    yield Publish(Signal(1))
    return None


def subscribed_woken_steps(many: bool) -> int:
    """With a handler with per-subscriber queues, the steps of one wait where the waiter waits first and is woken by another task's signal (the difference from the same assembly without a waiter)."""

    @do
    def tasks(with_waiter: bool):
        bus = EventBus()
        waiter = with_handlers([subscribed_event_handler(bus, "w", (Signal,))], waiting(many))
        spawned = ((yield Spawn(waiter)),) if with_waiter else ()
        sender = yield Spawn(with_handlers([subscribed_event_handler(bus, "p", (Signal,))], publishing()))
        yield Wait(sender)
        for task in spawned:
            yield Wait(task)
        return None

    return steps_of(tasks(True)) - steps_of(tasks(False))


def shared_woken_steps() -> int:
    """With ``event_handler``, which has no queues, the steps of one wait that is likewise woken after waiting (the difference from the same assembly without a waiter)."""

    @do
    def tasks(with_waiter: bool):
        spawned = ((yield Spawn(waiting(False))),) if with_waiter else ()
        sender = yield Spawn(publishing())
        yield Wait(sender)
        for task in spawned:
            yield Wait(task)
        return None

    return steps_of(with_handlers([event_handler()], tasks(True))) - steps_of(
        with_handlers([event_handler()], tasks(False))
    )


@pytest.mark.parametrize("scheduler", SCHEDULERS)
def test_a_woken_wait_for_event_costs_no_more_than_the_shared_handler_and_the_waiters_own_handler(
    scheduler: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Compare by upper bound: the Python version has exactly one extra step for wrapping the subscriber's handler, and the Rust version does not count the wrapping step so the counts are equal
    # (after making Rust the default, the equality comparison failed at 29 == 30 in the daily run). The 3 steps of the helper Program
    # exceed the bound in either version.
    monkeypatch.setenv(IMPLEMENTATION_ENV_VAR, scheduler)
    assert subscribed_woken_steps(many=False) <= shared_woken_steps() + WAITER_HANDLER_STEPS


@pytest.mark.parametrize("scheduler", SCHEDULERS)
def test_a_woken_wait_for_events_costs_the_same_as_a_woken_wait_for_event(
    scheduler: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(IMPLEMENTATION_ENV_VAR, scheduler)
    assert subscribed_woken_steps(many=True) == subscribed_woken_steps(many=False)
