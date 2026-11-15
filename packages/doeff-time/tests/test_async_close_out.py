"""A run that ends with a task parked on the wall-clock handler closes it without an ignored GeneratorExit.

When the root body returns, the root close-out lets go of what is still parked in the scheduler
(``SchedulerCore.end_run``), and Python closes the suspended handler generators it held. A generator
that performs an effect while it is closed (``finally: yield Cancel(timer)`` of a timed wait) yields
after GeneratorExit: CPython reports "Exception ignored while closing generator … RuntimeError:
generator ignored GeneratorExit", which a job's end log then shows as if the job had failed (it was
read as the cause of a screen job's failure). A closed handler has no run left to perform effects in,
so it lets go without yielding — the abandoned daemon timer goes with the run (#501).
"""

import asyncio
import gc
import sys
from collections.abc import Awaitable, Callable

import pytest
from doeff_core_effects import (
    CreateExternalPromise,
    ExternalPromise,
    SchedulerImplementation,
    Spawn,
    Wait,
    shielded,
)
from doeff_time import Delay, WaitWithin, async_time_handler
from time_test_support import run_with_handlers

from doeff import do
from doeff import handler as program_handler

IMPLEMENTATIONS: tuple[SchedulerImplementation, ...] = ("python", "rust")

# Far beyond the run: the parked waits never end on their own, the run's end abandons them.
NEVER_SECONDS = 1000.0


@do
def _parked_on_delay():
    yield Delay(NEVER_SECONDS)


@do
def _parked_within():
    never = yield CreateExternalPromise()
    yield WaitWithin(never.future, NEVER_SECONDS)


@do
def _parked_shielded():
    yield shielded(_parked_on_delay())


def _sleep_telling_parked(parked: ExternalPromise[None]) -> Callable[[float], Awaitable[None]]:
    """The handler's wall-clock sleep: completes ``parked`` once the wait is on the loop, then never ends.

    The only clock wait of each scenario starts after its handler generator is suspended (the Delay's
    own Await, or a timed wait's timer task once the handler parked in its Race), so ``parked`` tells
    the root that the generator is parked.
    """

    async def sleep(_seconds: float) -> None:
        parked.complete(None)
        await asyncio.get_running_loop().create_future()

    return sleep


@do
def _root_returning_while_parked(scenario: Callable[[], object]):
    """Spawn a daemon task that parks on the wall-clock handler, wait until it parked, then return."""
    parked = yield CreateExternalPromise()
    clock = program_handler(async_time_handler(sleep=_sleep_telling_parked(parked)))

    @do
    def body():
        yield Spawn(scenario(), daemon=True)
        yield Wait(parked.future)
        return "returned"

    return (yield clock(body()))


def _unraisable_while_running(scenario: Callable[[], object], monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Run a root that returns while a daemon task is parked; the unraisable reports CPython made meanwhile."""
    seen: list[str] = []
    monkeypatch.setattr(
        sys,
        "unraisablehook",
        lambda unraisable: seen.append(f"{unraisable.err_msg} {unraisable.object!r}: {unraisable.exc_value!r}"),
    )
    assert run_with_handlers(_root_returning_while_parked(scenario)) == "returned"
    gc.collect()
    return seen


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
@pytest.mark.parametrize(
    "scenario",
    [_parked_on_delay, _parked_within, _parked_shielded],
    ids=["delay", "wait_within", "shielded_delay"],
)
def test_a_run_ending_with_a_parked_wall_clock_wait_reports_no_ignored_generator_exit(
    implementation: SchedulerImplementation,
    scenario: Callable[[], object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOEFF_SCHEDULER", implementation)
    assert _unraisable_while_running(scenario, monkeypatch) == []
