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
import time
from typing import Any

import pytest

from doeff import (
    AcquireSemaphore,
    CreateSemaphore,
    Gather,
    ReleaseSemaphore,
    Spawn,
    WithHandler,
    default_handlers,
    do,
    run,
    Await,
    slog,
)
# REMOVED: from doeff_core_effects.handlers import sqlite_cache_handler


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
        prog = WithHandler(
            sqlite_cache_handler(None),
            _spawn_gather(factory, n, conc),
        )
        rss_before = _rss_mb()
        r = run(prog, handlers=default_handlers())
        rss_after = _rss_mb()
        return r, rss_before, rss_after
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


@pytest.mark.skip(reason="uses removed API: sqlite_cache_handler")
class TestAwaitHttpMemory:
    def test_sleep_500_low_memory(self):
        """Control: asyncio.sleep return is None — memory should be low."""
        r, rss_before, rss_after = _run_test(_sleep_task, 500)
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == 500
        delta = rss_after - rss_before
        print(f"\n  sleep N=500: RSS {rss_before:.0f} -> {rss_after:.0f} MB (delta={delta:.0f})")
        assert delta < 1000, f"Memory too high for sleep: {delta:.0f}MB"

    def test_http_500_memory(self):
        """BUG: Await returning ~10KB object — memory should not explode.

        If continuations retain the return value, memory grows as
        N * continuation_chain_size * return_value_size.
        """
        r, rss_before, rss_after = _run_test(_http_task, 500)
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == 500
        delta = rss_after - rss_before
        print(f"\n  http N=500: RSS {rss_before:.0f} -> {rss_after:.0f} MB (delta={delta:.0f})")
        # 500 * 10KB = 5MB. Allow 500MB for overhead. If >2GB, it's the bug.
        assert delta < 2000, (
            f"Memory leak! http N=500 used {delta:.0f}MB "
            f"(expected <500MB, sleep uses <200MB for same N)"
        )
