"""Reproducer: Await(real HTTP) + Spawn/Gather leaks memory via continuations.

With asyncio.sleep, N=750 uses ~500MB. With real HTTP (same latency), N=300
uses 34GB. The Await return value is retained in the continuation chain and
never GCed.

The HTTP response object itself is ~18KB (cloudpickle). But the continuation
chain holds closures referencing the full handler stack, amplifying retention.

See: https://github.com/proboscis/doeff/issues/355
"""

from __future__ import annotations

import asyncio
import resource
import signal
from typing import Any

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


def _timeout_handler(signum: int, frame: Any) -> None:
    raise TimeoutError(f"Deadlock detected after {TIMEOUT_SECONDS}s")


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


# --- Control: asyncio.sleep (tiny return value) ---

@do
def _sleep_task(i: int):
    for j in range(7):
        yield slog(msg=f"[{i}] pre-{j}", level="debug")
    yield Await(asyncio.sleep(0.01))
    for j in range(7):
        yield slog(msg=f"[{i}] post-{j}", level="debug")
    return f"done-{i}"


# --- Test: Await returning large-ish object ---

async def _fake_http(i: int) -> dict:
    """Simulate HTTP response: ~10KB dict (like a parsed API response)."""
    await asyncio.sleep(0.01)
    return {
        "id": f"resp-{i}",
        "choices": [{"message": {"content": "x" * 5000, "role": "assistant"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 500, "total_tokens": 600},
        "model": "test-model",
        "reasoning_tokens": list(range(200)),  # simulate reasoning token metadata
    }


@do
def _http_task(i: int):
    for j in range(7):
        yield slog(msg=f"[{i}] pre-{j}", level="debug")
    result = yield Await(_fake_http(i))
    for j in range(7):
        yield slog(msg=f"[{i}] post-{j}", level="debug")
    return result["id"]


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


def _run_test(factory, n: int, conc: int = 40):
    old = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TIMEOUT_SECONDS)
    try:
        prog = sqlite_cache_handler(None)(_spawn_gather(factory, n, conc))
        rss_before = _rss_mb()
        r = run_with_defaults(prog)
        rss_after = _rss_mb()
        return r, rss_before, rss_after
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
