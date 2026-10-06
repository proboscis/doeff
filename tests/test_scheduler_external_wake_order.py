"""Where a task woken by an external completion runs (agora-redesign #3861).

An external completion (``ExternalPromise.complete`` from any thread) wakes
its waiter at the next scheduler effect. The woken waiter must not queue
behind every runnable task of its priority: with N runnable tasks it would
wait N steps, so every ``Await`` paid the CPU of the whole backlog. Same-
priority runnable work still progresses (no starvation), priorities still
dominate, and daemon tasks stay shielded behind a pending external wait.

Both scheduler implementations must agree, so every case runs on each.
"""

import threading
import time

import pytest
from doeff_core_effects.scheduler import (
    PRIORITY_HIGH,
    PRIORITY_IDLE,
    CompletePromise,
    CreateExternalPromise,
    CreatePromise,
    ExternalPromise,
    Gather,
    Spawn,
    Wait,
    scheduled,
)

from doeff import do
from doeff import run as doeff_run

IMPLEMENTATIONS = pytest.mark.parametrize("implementation", ["python", "rust"])


@do
def _rotate():
    """One scheduler round trip that re-queues the caller behind its peers."""
    promise = yield CreatePromise()
    yield CompletePromise(promise, None)


@IMPLEMENTATIONS
def test_external_wake_does_not_wait_for_the_runnable_backlog(implementation: str) -> None:
    """Eight runnable tasks rotate; one of them completes the waiter's external
    promise. The waiter runs before the other seven take their next step."""
    log: list[object] = []
    busy_count = 8
    steps = busy_count + 3

    @do
    def busy(index: int, ep: ExternalPromise[str]):
        for step in range(steps):
            log.append((index, step))
            if index == 0 and step == busy_count:
                ep.complete("done")
                log.append("completed")
            yield _rotate()
        return index

    @do
    def waiter_task(ep: ExternalPromise[str]):
        value = yield Wait(ep.future)
        log.append("woken")
        return value

    @do
    def body():
        ep = yield CreateExternalPromise()
        waiter = yield Spawn(waiter_task(ep))
        workers = ()
        for index in range(busy_count):
            workers = (*workers, (yield Spawn(busy(index, ep))))
        return (yield Gather(waiter, *workers))

    result = doeff_run(scheduled(body(), implementation=implementation))

    assert result == ["done", *range(busy_count)]
    between = log[log.index("completed") + 1 : log.index("woken")]
    assert between == [], (
        f"the woken waiter ran after {len(between)} busy steps: {between}"
    )


@IMPLEMENTATIONS
def test_foreign_thread_wake_runs_within_two_busy_steps(implementation: str) -> None:
    """A completion from another thread lands while eight CPU-bound tasks
    rotate; the waiter runs within two busy steps of the completion."""
    busy_count = 8
    steps = 30
    step_seconds = 0.002
    taken = [0]
    at_complete = [0]
    at_wake = [0]
    release = threading.Event()

    def completer(ep: ExternalPromise[str]) -> None:
        release.wait(5)
        at_complete[0] = taken[0]
        ep.complete("done")

    @do
    def waiter(ep: ExternalPromise[str]):
        value = yield Wait(ep.future)
        at_wake[0] = taken[0]
        return value

    @do
    def busy():
        for _ in range(steps):
            deadline = time.perf_counter() + step_seconds
            while time.perf_counter() < deadline:
                pass
            taken[0] += 1
            if taken[0] >= busy_count * 2:
                release.set()
            yield _rotate()

    @do
    def body():
        ep = yield CreateExternalPromise()
        threading.Thread(target=completer, args=(ep,), daemon=True).start()
        handle = yield Spawn(waiter(ep))
        workers = ()
        for _ in range(busy_count):
            workers = (*workers, (yield Spawn(busy())))
        yield Gather(*workers)
        return (yield Wait(handle))

    assert doeff_run(scheduled(body(), implementation=implementation)) == "done"
    steps_waited = at_wake[0] - at_complete[0]
    assert steps_waited <= 2, f"the woken waiter waited {steps_waited} busy steps"


@IMPLEMENTATIONS
def test_external_wakes_alternate_with_runnable_work(implementation: str) -> None:
    """Twenty waiters are woken externally at once while a runnable task of
    the same priority keeps rotating: the woken waiters run one between two
    of its steps, so neither side starves the other."""
    waiter_count = 20
    log: list[object] = []

    @do
    def waiter_task(ep: ExternalPromise[None]):
        yield Wait(ep.future)
        log.append("woken")

    @do
    def rotating(eps: tuple[ExternalPromise[None], ...]):
        for ep in eps:
            ep.complete(None)
        for step in range(waiter_count + 2):
            log.append(("rotating", step))
            yield _rotate()

    @do
    def body():
        eps = ()
        waiters = ()
        for _ in range(waiter_count):
            ep = yield CreateExternalPromise()
            eps = (*eps, ep)
            waiters = (*waiters, (yield Spawn(waiter_task(ep))))
        worker = yield Spawn(rotating(eps))
        yield Gather(worker, *waiters)

    doeff_run(scheduled(body(), implementation=implementation))
    runs = "".join("w" if item == "woken" else "r" for item in log).split("r")
    assert max(len(run) for run in runs) <= 1, (
        f"woken waiters between two rotating steps: {[len(run) for run in runs]}"
    )
    assert log.count("woken") == waiter_count


@IMPLEMENTATIONS
def test_external_wake_keeps_priority_order(implementation: str) -> None:
    """An externally woken NORMAL waiter still runs after runnable HIGH work,
    and an externally woken IDLE waiter after runnable NORMAL work."""
    log: list[object] = []
    steps = 3

    @do
    def busy(name: str, ep: ExternalPromise[str]):
        for step in range(steps):
            log.append((name, step))
            if step == 0:
                ep.complete(name)
            yield _rotate()

    @do
    def waiter(name: str, ep: ExternalPromise[str]):
        yield Wait(ep.future)
        log.append(name)

    @do
    def body():
        normal_ep = yield CreateExternalPromise()
        idle_ep = yield CreateExternalPromise()
        tasks = [
            (yield Spawn(waiter("normal woken", normal_ep))),
            (yield Spawn(waiter("idle woken", idle_ep), priority=PRIORITY_IDLE)),
            (yield Spawn(busy("high", normal_ep), priority=PRIORITY_HIGH)),
            (yield Spawn(busy("normal", idle_ep))),
        ]
        yield Gather(*tasks)

    doeff_run(scheduled(body(), implementation=implementation))
    assert log.index("normal woken") > log.index(("high", steps - 1))
    assert log.index("idle woken") > log.index(("normal", steps - 1))


@IMPLEMENTATIONS
def test_externally_woken_daemon_stays_behind_a_pending_external_wait(
    implementation: str,
) -> None:
    """A daemon woken by an external completion stays shielded while another
    task's external wait is pending, even when non-daemon work runs below the
    shield (#505)."""
    log: list[object] = []

    @do
    def pending_waiter(ep: ExternalPromise[None]):
        yield Wait(ep.future)
        log.append("pending resolved")

    @do
    def daemon_waiter(ep: ExternalPromise[None]):
        yield Wait(ep.future, priority=PRIORITY_IDLE)
        log.append("daemon woken")

    @do
    def idle_worker(pending_ep: ExternalPromise[None], daemon_ep: ExternalPromise[None]):
        daemon_ep.complete(None)
        for step in range(3):
            log.append(("idle", step))
            yield _rotate()
        log.append("completing pending")
        pending_ep.complete(None)

    @do
    def body():
        pending_ep = yield CreateExternalPromise()
        daemon_ep = yield CreateExternalPromise()
        daemon = yield Spawn(daemon_waiter(daemon_ep), priority=PRIORITY_IDLE, daemon=True)
        waiting = yield Spawn(pending_waiter(pending_ep))
        worker = yield Spawn(
            idle_worker(pending_ep, daemon_ep), priority=PRIORITY_IDLE
        )
        yield Gather(waiting, worker, daemon)

    doeff_run(scheduled(body(), implementation=implementation))
    assert log.index("daemon woken") > log.index("completing pending")
