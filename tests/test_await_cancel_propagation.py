"""Cancel propagation into in-flight external work (#498 known limitation).

Cancelling a doeff task that is parked on an external promise must reach
whoever produces that promise:

- scheduler level: ``ExternalPromise.on_cancel(callback)`` registers a
  cancel callback; when a ``Cancel`` removes the LAST live waiter of a
  pending external promise, the scheduler marks the promise ``cancelled``
  (so a late completion is ignored) and runs the callbacks.
- ``await_handler``: registers the ``run_coroutine_threadsafe`` future's
  ``cancel`` as the callback, so the bridged coroutine receives
  ``asyncio.CancelledError`` and its ``finally`` runs.

Race semantics are unchanged: Race losers keep running until they are
cancelled explicitly (docs/04-async-effects.md "Race Semantics").
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest
from doeff_core_effects import Await, await_handler
from doeff_core_effects.scheduler import (
    Cancel,
    CreateExternalPromise,
    ExternalPromiseCancelCallbackError,
    Gather,
    Race,
    Spawn,
    TaskCancelledError,
    Wait,
    scheduled,
)

from doeff import do
from doeff import run as doeff_run

RUN_TIMEOUT_SECONDS = 5.0
UNWIND_TIMEOUT_SECONDS = 2.0


def _run_await(program: Any) -> Any:
    """Run ``program`` under scheduler + await_handler, failing on a hang."""
    result: dict[str, Any] = {}

    def _worker() -> None:
        try:
            result["value"] = doeff_run(scheduled(await_handler()(program)))
        except BaseException as exc:  # re-raised below on the test thread
            result["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=RUN_TIMEOUT_SECONDS)
    assert not thread.is_alive(), "scheduler run hung"
    if "error" in result:
        raise result["error"]
    return result["value"]


async def _until(event: threading.Event) -> None:
    assert await asyncio.to_thread(event.wait, RUN_TIMEOUT_SECONDS)


async def _forever() -> None:
    """Suspend until cancelled (stands in for long-running I/O)."""
    await asyncio.Event().wait()


class _Probe:
    """Records what a long-running bridged coroutine observed."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.finalized = threading.Event()
        self.saw_cancelled = threading.Event()

    async def long_io(self) -> str:
        self.started.set()
        try:
            await _forever()
            return "finished"
        except asyncio.CancelledError:
            self.saw_cancelled.set()
            raise
        finally:
            self.finalized.set()


# ---------------------------------------------------------------------------
# await_handler: the bridged coroutine is cancelled
# ---------------------------------------------------------------------------


def test_cancel_during_await_cancels_bridged_coroutine() -> None:
    """(a) Cancelling a task parked on Await delivers CancelledError to the
    bridged coroutine and runs its finally."""
    probe = _Probe()

    @do
    def worker():
        return (yield Await(probe.long_io()))

    @do
    def body():
        task = yield Spawn(worker())
        yield Await(_until(probe.started))
        yield Cancel(task)
        try:
            yield Wait(task)
        except TaskCancelledError:
            return "cancelled"
        return "not cancelled"

    assert _run_await(body()) == "cancelled"
    assert probe.finalized.wait(UNWIND_TIMEOUT_SECONDS), (
        "bridged coroutine kept running after its doeff task was cancelled"
    )
    assert probe.saw_cancelled.is_set()


def test_cancel_right_after_parking_still_reaches_coroutine() -> None:
    """(a') Cancel may land before the bridged coroutine took its first step
    on the loop. run_coroutine_threadsafe queues the cancel behind that first
    step, so the coroutine still starts, then sees CancelledError at its
    first suspension — it is never left un-awaited or running."""
    probe = _Probe()

    @do
    def worker():
        return (yield Await(probe.long_io()))

    @do
    def body():
        task = yield Spawn(worker())
        # Let the worker park on its Await without waiting for its coroutine
        # to start on the loop.
        yield Await(asyncio.sleep(0))
        yield Cancel(task)
        return "requested"

    assert _run_await(body()) == "requested"
    assert probe.finalized.wait(UNWIND_TIMEOUT_SECONDS)
    assert probe.saw_cancelled.is_set()


def test_late_completion_after_cancel_is_ignored() -> None:
    """(b) A bridged coroutine that swallows the cancel and completes late
    must not resurrect the cancelled task or break later scheduling."""
    started = threading.Event()
    late_done = threading.Event()

    async def stubborn():
        started.set()
        try:
            await _forever()
        except asyncio.CancelledError:
            # Swallow the cancel and keep going: completes AFTER the cancel.
            await asyncio.to_thread(lambda: None)
        late_done.set()
        return "late"

    @do
    def worker():
        return (yield Await(stubborn()))

    @do
    def body():
        task = yield Spawn(worker())
        yield Await(_until(started))
        yield Cancel(task)
        # Keep the run alive past the late completion: the late ep.complete
        # runs in the same loop step as late_done.set(), so it is queued
        # before this Await's own completion.
        yield Await(_until(late_done))
        after = yield Await(asyncio.sleep(0, result="still scheduling"))
        try:
            yield Wait(task)
        except TaskCancelledError:
            return ("cancelled", after)
        return ("resurrected", after)

    assert _run_await(body()) == ("cancelled", "still scheduling")


def test_cancel_leaves_other_awaits_untouched() -> None:
    """(c) Only the cancelled task's coroutine is cancelled; a sibling Await
    completes normally with its own value."""
    victim = _Probe()
    sibling_finalized = threading.Event()
    release = threading.Event()

    async def sibling_io():
        try:
            await _until(release)
            return "sibling-ok"
        finally:
            sibling_finalized.set()

    @do
    def victim_task():
        return (yield Await(victim.long_io()))

    @do
    def sibling_task():
        return (yield Await(sibling_io()))

    @do
    def body():
        v = yield Spawn(victim_task())
        s = yield Spawn(sibling_task())
        yield Await(_until(victim.started))
        yield Cancel(v)
        yield Await(_until(victim.finalized))
        cancelled_early = sibling_finalized.is_set()
        release.set()
        value = yield Wait(s)
        return value, cancelled_early

    assert _run_await(body()) == ("sibling-ok", False)
    assert victim.saw_cancelled.is_set()


def test_cancel_race_loser_parked_on_await() -> None:
    """(d) Race does not cancel losers (documented semantics); an explicit
    Cancel of the loser parked on Await reaches its bridged coroutine."""
    loser = _Probe()

    @do
    def slow():
        return (yield Await(loser.long_io()))

    @do
    def fast():
        yield Await(_until(loser.started))
        return "fast"

    @do
    def body():
        t_slow = yield Spawn(slow())
        t_fast = yield Spawn(fast())
        winner = yield Race(t_slow, t_fast)
        loser_alive_after_race = not loser.finalized.is_set()
        yield Cancel(t_slow)
        return winner, loser_alive_after_race

    assert _run_await(body()) == ("fast", True)
    assert loser.finalized.wait(UNWIND_TIMEOUT_SECONDS)
    assert loser.saw_cancelled.is_set()


# ---------------------------------------------------------------------------
# Scheduler: ExternalPromise.on_cancel for any external producer
# ---------------------------------------------------------------------------


def test_on_cancel_runs_when_last_waiter_is_cancelled() -> None:
    calls: list[str] = []

    @do
    def waiter(ep):
        return (yield Wait(ep.future))

    @do
    def body():
        ep = yield CreateExternalPromise()
        ep.on_cancel(lambda: calls.append("cancel"))
        task = yield Spawn(waiter(ep))
        yield Await(asyncio.sleep(0))  # let the waiter park
        yield Cancel(task)
        ep.complete("late")  # a late completion is ignored
        try:
            yield Wait(ep.future)
        except TaskCancelledError:
            return list(calls)
        return "promise was not cancelled"

    assert _run_await(body()) == ["cancel"]


def test_on_cancel_skipped_while_another_waiter_is_live() -> None:
    calls: list[str] = []

    @do
    def waiter(ep):
        return (yield Wait(ep.future))

    @do
    def body():
        ep = yield CreateExternalPromise()
        ep.on_cancel(lambda: calls.append("cancel"))
        first = yield Spawn(waiter(ep))
        second = yield Spawn(waiter(ep))
        yield Await(asyncio.sleep(0))
        yield Cancel(first)
        ep.complete("value")
        return (yield Wait(second)), list(calls)

    assert _run_await(body()) == ("value", [])


def test_on_cancel_reaches_gather_waiter() -> None:
    calls: list[str] = []

    @do
    def gatherer(ep):
        return (yield Gather(ep.future))

    @do
    def body():
        ep = yield CreateExternalPromise()
        ep.on_cancel(lambda: calls.append("cancel"))
        task = yield Spawn(gatherer(ep))
        yield Await(asyncio.sleep(0))
        yield Cancel(task)
        return list(calls)

    assert _run_await(body()) == ["cancel"]


def test_on_cancel_not_called_for_settled_promise() -> None:
    calls: list[str] = []

    @do
    def body():
        ep = yield CreateExternalPromise()
        ep.on_cancel(lambda: calls.append("cancel"))
        ep.complete("done")
        value = yield Wait(ep.future)
        ep.on_cancel(lambda: calls.append("late-registration"))
        return value, list(calls)

    assert _run_await(body()) == ("done", [])


def test_failing_cancel_callback_surfaces_to_cancel_caller() -> None:
    boom = ValueError("canceller failed")

    def canceller():
        raise boom

    @do
    def waiter(ep):
        return (yield Wait(ep.future))

    @do
    def body():
        ep = yield CreateExternalPromise()
        ep.on_cancel(canceller)
        task = yield Spawn(waiter(ep))
        yield Await(asyncio.sleep(0))
        try:
            yield Cancel(task)
        except ExternalPromiseCancelCallbackError as exc:
            cancelled = True
            try:
                yield Wait(task)
                cancelled = False
            except TaskCancelledError:
                pass
            return exc.errors, cancelled
        return "no error"

    errors, cancelled = _run_await(body())
    assert errors == [boom]
    assert cancelled


def test_on_cancel_requires_callable() -> None:
    @do
    def body():
        ep = yield CreateExternalPromise()
        not_callable: Any = "not callable"  # a misuse the runtime must reject
        ep.on_cancel(not_callable)

    with pytest.raises(TypeError, match="callable"):
        _run_await(body())
