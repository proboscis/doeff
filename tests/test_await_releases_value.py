"""await_handler hands the awaited value over without leaving it in the scheduler.

The scheduler keeps a completed promise's result in its promise table until the
terminal-entry sweep finds every handle dead, and that sweep runs once per
``HANDLE_SWEEP_INTERVAL`` (1024) id allocations (#502). An Await used to
complete its promise with the awaited value itself, so up to ~1024 awaited
values stayed alive after their awaiters had dropped them: a job reading 238
pages of ~3 MiB over HTTP grew its RSS from 131 to 895 MiB, and gc could not
reclaim it (the table is the scheduler's, not a cycle).

await_handler is the only holder of the promise it creates (the future is minted
inside the handler and given only to its own Wait), so it hands the value over
in a one-slot box and empties the box when it resumes the awaiter: the table
keeps only an empty box, and the value lives exactly as long as the awaiter
keeps it.
"""

from __future__ import annotations

import gc
import weakref

import pytest
from doeff_core_effects import Await, await_handler
from doeff_core_effects.scheduler import scheduled

from doeff import do, run, with_handlers

# Fewer than HANDLE_SWEEP_INTERVAL, so no terminal-entry sweep runs in between.
AWAITS = 20


class Payload:
    """An awaited value whose liveness a weakref can watch."""


async def _make() -> Payload:
    return Payload()


@pytest.mark.parametrize("implementation", ["rust", "python"])
def test_an_awaited_value_is_freed_once_the_awaiter_drops_it(implementation: str) -> None:
    # Failure case: before the fix every awaited Payload stays alive in the
    # scheduler's promise table after the program dropped it, so all AWAITS
    # weakrefs are still alive inside the run. After it only the latest one
    # may be: the VM keeps the value it last handed over until the next
    # hand-over (a plain Transfer without the scheduler does the same) — one
    # value, not one per Await.
    @do
    def program():
        payload_refs: tuple[weakref.ref[Payload], ...] = ()
        for _ in range(AWAITS):
            value = yield Await(_make())
            payload_refs = (*payload_refs, weakref.ref(value))
            del value
        gc.collect()
        return [index for index, ref in enumerate(payload_refs) if ref() is not None]

    alive = run(
        scheduled(with_handlers([await_handler()], program()), implementation=implementation)
    )

    assert set(alive) <= {AWAITS - 1}, f"awaited values {alive} of {AWAITS} are still alive"


@pytest.mark.parametrize("implementation", ["rust", "python"])
def test_the_awaiter_still_receives_the_value_and_the_failure(implementation: str) -> None:
    async def answer() -> dict[str, int]:
        return {"n": 1}

    async def broken() -> None:
        raise ValueError("broken")

    @do
    def program():
        value = yield Await(answer())
        try:
            yield Await(broken())
        except ValueError as error:
            return value, str(error)
        return value, None

    got = run(scheduled(with_handlers([await_handler()], program()), implementation=implementation))

    assert got == ({"n": 1}, "broken")
