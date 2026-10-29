"""offloaded hands its answer over without leaving it in the scheduler.

The scheduler keeps a completed promise's result in its promise table until the
terminal-entry sweep finds every handle dead, and that sweep runs once per
``HANDLE_SWEEP_INTERVAL`` (1024) id allocations (#502). ``offloaded`` (the
off-scheduler call behind the ClickHouse, PostgreSQL and file handlers) used to
complete its promise with the call's answer itself, so answers the caller had
already dropped stayed alive until a sweep: a log-reading job kept every
ClickHouse ``SqlRows`` it had answered and grew its RSS from 440 to 2464 MiB in
3.5 hours. The Rust scheduler's table is not visible to gc either.

``offloaded`` now completes the promise with a one-slot box (``Delivered``) and
empties it on hand-over — the same shape as ``await_handler``'s
``_AwaitedValue`` (tests/test_await_releases_value.py).
"""

from __future__ import annotations

import gc
import weakref

import pytest
from doeff_core_effects import scheduled
from doeff_core_effects.offloaded_call import ThreadPerCall, offloaded

from doeff import do, run

# Fewer than HANDLE_SWEEP_INTERVAL, so no terminal-entry sweep runs in between.
CALLS = 20


class Payload:
    """An answer whose liveness a weakref can watch."""


def _keep_nothing(_value: object) -> None:
    return None


@pytest.mark.parametrize("implementation", ["rust", "python"])
def test_an_offloaded_answer_is_freed_once_the_caller_drops_it(implementation: str) -> None:
    # Failure case: before the fix every Payload stays alive in the scheduler's
    # promise table after the program dropped it, so all CALLS weakrefs are
    # alive inside the run. After it only the latest one may be: the VM keeps
    # the value it last handed over until the next hand-over.
    pool = ThreadPerCall()

    @do
    def program():
        payload_refs: tuple[weakref.ref[Payload], ...] = ()
        for _ in range(CALLS):
            value = yield offloaded(pool, Payload, _keep_nothing)
            payload_refs = (*payload_refs, weakref.ref(value))
            del value
        gc.collect()
        return [index for index, ref in enumerate(payload_refs) if ref() is not None]

    alive = run(scheduled(program(), implementation=implementation))

    assert set(alive) <= {CALLS - 1}, f"offloaded answers {alive} of {CALLS} are still alive"


@pytest.mark.parametrize("implementation", ["rust", "python"])
def test_the_caller_still_receives_the_answer_and_the_failure(implementation: str) -> None:
    pool = ThreadPerCall()

    def broken() -> None:
        raise ValueError("broken")

    @do
    def program():
        value = yield offloaded(pool, lambda: {"n": 1}, _keep_nothing)
        none = yield offloaded(pool, lambda: None, _keep_nothing)
        try:
            yield offloaded(pool, broken, _keep_nothing)
        except ValueError as error:
            return value, none, str(error)
        return value, none, None

    got = run(scheduled(program(), implementation=implementation))

    assert got == ({"n": 1}, None, "broken")
