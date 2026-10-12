"""Regression guard for doeff #355: what an ``Await`` returns must be released.

#355: Spawn + Gather + Await(real HTTP) grew to 34GB. The response an ``Await``
handed back stayed reachable from the continuation chain after its task was
done, so every response of the run was pinned at once (a parsed GPT-5 response
is large, and the chain amplified it).

The first version of this file measured ``ru_maxrss`` before and after a run.
That cannot see a leak: ``ru_maxrss`` is the process's high-water mark (it never
falls, so a test run earlier in the same process hides this one), and on Linux
it is in KB while ``_rss_mb`` divided it as bytes, so the delta printed 0.
Here the awaited coroutine returns an object a weakref can watch and the test
asks whether it is gone: after the run and ``gc.collect()`` every response must
be dead. A response something still holds (a continuation that kept the value, a
cycle the collector cannot see through) stays alive and the test is red.

What turns it red today: the OOM went away with #354 (4dd01c6cb), but the VM that
fix lived in was replaced by the 2026-03-25 rebuild (a2e31aa01), so it cannot be
reverted any more. Its present-day counterpart is the GC protocol on the VM's
pyclasses (a119734b7, #500): with ``visit_py_field`` (doeff-vm/src/gc.rs) made a
no-op, all 500 responses of the python scheduler's run survive ``gc.collect()``.
The rust-scheduler runs stay green under that change; they guard the Rust
scheduler's own state.

Not covered: during the run the scheduler keeps a finished Await's value in its
promise table until its terminal-entry sweep (every ``HANDLE_SWEEP_INTERVAL``
ids, #502) or the end of the run. That is the scheduler's own bookkeeping, not
the continuation chain; this file looks at what outlives the run.

See: https://github.com/proboscis/doeff/issues/355
"""

from __future__ import annotations

import asyncio
import gc
import signal
import weakref
from typing import Any

import pytest
from doeff_core_effects.cache_handlers import sqlite_cache_handler

from doeff import (
    AcquireSemaphore,
    Await,
    CreateSemaphore,
    Gather,
    ReleaseSemaphore,
    Spawn,
    do,
    slog,
)
from tests._run_helpers import run_with_defaults

TIMEOUT_SECONDS = 120

N_TASKS = 500
CONCURRENCY = 40


@pytest.fixture(params=["python", "rust"])
def scheduler_implementation(request, monkeypatch):
    """Run each test under both schedulers (``scheduled`` reads the env per call)."""
    monkeypatch.setenv("DOEFF_SCHEDULER", request.param)
    return request.param


def _timeout_handler(signum: int, frame: Any) -> None:
    raise TimeoutError(f"Deadlock detected after {TIMEOUT_SECONDS}s")


class _Response:
    """Stand-in for a parsed HTTP response (~10KB, like a parsed API response).

    A plain class, not a dict: instances can be the target of a weakref.
    """

    def __init__(self, i: int) -> None:
        self.id = f"resp-{i}"
        self.choices = [{"message": {"content": "x" * 5000, "role": "assistant"}}]
        self.usage = {"prompt_tokens": 100, "completion_tokens": 500, "total_tokens": 600}
        self.model = "test-model"
        self.reasoning_tokens = list(range(200))  # simulate reasoning token metadata


# --- Control: Await of a coroutine that returns nothing ---

@do
def _sleep_task(i: int):
    for j in range(7):
        yield slog(msg=f"[{i}] pre-{j}", level="debug")
    yield Await(asyncio.sleep(0))
    for j in range(7):
        yield slog(msg=f"[{i}] post-{j}", level="debug")
    return f"done-{i}"


# --- Await returning a large-ish object ---

async def _fake_http(i: int) -> _Response:
    """Simulate an HTTP response: the object exists only after the coroutine suspended once."""
    await asyncio.sleep(0)
    return _Response(i)


@do
def _http_task(i: int):
    """The task's result is the response's id and a weakref to the response, never the response."""
    for j in range(7):
        yield slog(msg=f"[{i}] pre-{j}", level="debug")
    result = yield Await(_fake_http(i))
    for j in range(7):
        yield slog(msg=f"[{i}] post-{j}", level="debug")
    return result.id, weakref.ref(result)


@do
def _http_task_keeping_response(i: int):
    """Like ``_http_task``, but the task's result holds the response itself."""
    result = yield Await(_fake_http(i))
    return result, weakref.ref(result)


@do
def _spawn_gather(factory, n: int, conc: int):
    sem = yield CreateSemaphore(conc)

    @do
    def _t(i):
        yield AcquireSemaphore(sem)
        r = yield factory(i)
        yield ReleaseSemaphore(sem)
        return r

    tasks = []
    for i in range(n):
        t = yield Spawn(_t(i), daemon=False)
        tasks.append(t)
    return list((yield Gather(*tasks)))


def _run_test(factory, n: int, conc: int = CONCURRENCY):
    old = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TIMEOUT_SECONDS)
    try:
        # ":memory:" — a None path would make a file literally named "None" in the cwd.
        prog = sqlite_cache_handler(":memory:")(_spawn_gather(factory, n, conc))
        return run_with_defaults(prog)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


class TestAwaitHttpMemory:
    def test_sleep_runs_every_task(self, scheduler_implementation):
        """Control: Await of a coroutine returning None completes all tasks."""
        r = _run_test(_sleep_task, N_TASKS)
        assert r.is_ok(), f"Failed: {r.error}"
        assert r.value == [f"done-{i}" for i in range(N_TASKS)]

    def test_http_responses_are_released_after_the_run(self, scheduler_implementation):
        """#355: once the run is over no response an Await returned is still alive."""
        r = _run_test(_http_task, N_TASKS)
        assert r.is_ok(), f"Failed: {r.error}"
        ids = [response_id for response_id, _ in r.value]
        refs = [ref for _, ref in r.value]
        assert ids == [f"resp-{i}" for i in range(N_TASKS)]

        gc.collect()
        survivors = [ref for ref in refs if ref() is not None]
        assert not survivors, (
            f"{len(survivors)} of {len(refs)} Await return values survived the run and "
            f"gc.collect(): something (the continuation chain, a cycle the collector "
            f"cannot see through) still holds them."
        )

    def test_watched_objects_are_the_ones_await_delivers(self, scheduler_implementation):
        """Control for the guard above: the weakrefs watch the objects Await delivered.

        Here each task's result holds its response, so the caller holds all of
        them: they are alive while the caller keeps the result and dead once it
        lets go. A guard that watched copies, or that read "dead" for objects
        still in use, would not pass this.
        """
        r = _run_test(_http_task_keeping_response, N_TASKS)
        assert r.is_ok(), f"Failed: {r.error}"
        assert [response.id for response, _ in r.value] == [f"resp-{i}" for i in range(N_TASKS)]
        refs = [ref for _, ref in r.value]

        gc.collect()
        assert all(ref() is not None for ref in refs)

        del r
        gc.collect()
        assert all(ref() is None for ref in refs)
