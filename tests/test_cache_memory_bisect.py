"""Bisect: which part of cache() causes O(N) memory blowup?

Tests isolate individual effects used by cache() to find the culprit.

Every task holds one external Await (``_AllHeldGate.hold``) that cannot finish until all N
tasks are holding theirs, so N waits are held at once by construction, not by timing.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

from doeff_core_effects._cache import FrozenDict
from doeff_vm import GetExecutionContext

from doeff import (
    Await,
    Gather,
    Spawn,
    Try,
    do,
    slog,
)
from tests._run_helpers import run_with_defaults

TIMEOUT_SECONDS = 60


def _timeout_handler(signum: int, frame: Any) -> None:
    raise TimeoutError(f"Deadlock detected after {TIMEOUT_SECONDS}s")


def _run_with_timeout(program):
    old = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TIMEOUT_SECONDS)
    try:
        return run_with_defaults(program)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _get_rss_mb() -> float:
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


N = 500


class _AllHeldGate:
    """No wait ends until ``n`` waits are held at once.

    A sleep of a few ms lets early waits end before late ones start, so how many were held at once
    varied run to run (measured with the sleeps: 11-168 of 500). Here the n-th wait to start opens the
    gate for everyone, so the count held at once is always ``n``; ``mut_peak_held`` is that observation.
    Awaits run on the one event loop that await_handler shares process-wide, so plain counters are safe.
    """

    def __init__(self, n: int) -> None:
        self._n = n
        self._mut_held = 0
        self.mut_peak_held = 0
        self._all_held = asyncio.Event()

    async def hold(self) -> None:
        self._mut_held += 1
        self.mut_peak_held = max(self.mut_peak_held, self._mut_held)
        if self._mut_held == self._n:
            self._all_held.set()
        try:
            await self._all_held.wait()
        finally:
            self._mut_held -= 1


@do
def _spawn_gather_n(factory, n: int):
    gate = _AllHeldGate(n)
    tasks = []
    for i in range(n):
        t = yield Spawn(factory(i, gate))
        tasks.append(t)
    results = list((yield Gather(*tasks)))
    assert gate.mut_peak_held == n, f"only {gate.mut_peak_held} of {n} waits were held at once"
    return results


# --- Control: just Await, no extras ---
@do
def _bare_await(i: int, gate: _AllHeldGate):
    yield Await(gate.hold())
    return f"done-{i}"


# --- Suspect 1: GetExecutionContext ---
@do
def _with_get_context(i: int, gate: _AllHeldGate):
    context = yield GetExecutionContext()
    _ = getattr(context, "active_chain", ())
    yield Await(gate.hold())
    return f"done-{i}"


# --- Suspect 2: nested @do (like cache wrapper) ---
@do
def _with_nested_do(i: int, gate: _AllHeldGate):
    @do
    def inner():
        yield Await(gate.hold())
        return f"done-{i}"
    return (yield inner())


# --- Suspect 3: Try wrapping ---
@do
def _with_try(i: int, gate: _AllHeldGate):
    @do
    def inner():
        yield Await(gate.hold())
        return f"done-{i}"
    result = yield Try(inner())
    return result.value


# --- Suspect 4: cloudpickle.dumps ---
@do
def _with_pickle(i: int, gate: _AllHeldGate):
    import cloudpickle
    key = ("test_func", (i,), FrozenDict())
    cloudpickle.dumps(key)
    yield Await(gate.hold())
    return f"done-{i}"


# --- Suspect 5: multiple nested @do (matching cache depth) ---
@do
def _cache_like_depth(i: int, gate: _AllHeldGate):
    """Mimics cache() nesting: wrapper -> build_key -> try_get -> compute_and_cache"""
    @do
    def build_key():
        return ("test", (i,), FrozenDict())

    @do
    def try_get(key):
        # Simulate cache miss
        @do
        def inner():
            raise KeyError("miss")
        r = yield Try(inner())
        return r

    @do
    def compute(key):
        yield slog(msg=f"compute {i}", level="DEBUG")
        yield Await(gate.hold())
        return f"done-{i}"

    yield GetExecutionContext()
    key = yield build_key()
    yield try_get(key)
    return (yield compute(key))


class TestMemoryBisect:
    def test_bare_await(self):
        """Control: bare Await. Should be fast and low memory."""
        rss_before = _get_rss_mb()
        r = _run_with_timeout(_spawn_gather_n(_bare_await, N))
        rss_after = _get_rss_mb()
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == N
        print(f"\n  bare_await: RSS {rss_before:.0f} -> {rss_after:.0f} MB (delta={rss_after-rss_before:.0f})")

    def test_get_context(self):
        """Suspect 1: GetExecutionContext"""
        rss_before = _get_rss_mb()
        r = _run_with_timeout(_spawn_gather_n(_with_get_context, N))
        rss_after = _get_rss_mb()
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == N
        print(f"\n  get_context: RSS {rss_before:.0f} -> {rss_after:.0f} MB (delta={rss_after-rss_before:.0f})")

    def test_nested_do(self):
        """Suspect 2: nested @do"""
        rss_before = _get_rss_mb()
        r = _run_with_timeout(_spawn_gather_n(_with_nested_do, N))
        rss_after = _get_rss_mb()
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == N
        print(f"\n  nested_do: RSS {rss_before:.0f} -> {rss_after:.0f} MB (delta={rss_after-rss_before:.0f})")

    def test_try_wrap(self):
        """Suspect 3: Try wrapping"""
        rss_before = _get_rss_mb()
        r = _run_with_timeout(_spawn_gather_n(_with_try, N))
        rss_after = _get_rss_mb()
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == N
        print(f"\n  try_wrap: RSS {rss_before:.0f} -> {rss_after:.0f} MB (delta={rss_after-rss_before:.0f})")

    def test_pickle(self):
        """Suspect 4: cloudpickle serialization"""
        rss_before = _get_rss_mb()
        r = _run_with_timeout(_spawn_gather_n(_with_pickle, N))
        rss_after = _get_rss_mb()
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == N
        print(f"\n  pickle: RSS {rss_before:.0f} -> {rss_after:.0f} MB (delta={rss_after-rss_before:.0f})")

    def test_cache_like_depth(self):
        """Suspect 5: full cache-like nesting depth"""
        rss_before = _get_rss_mb()
        r = _run_with_timeout(_spawn_gather_n(_cache_like_depth, N))
        rss_after = _get_rss_mb()
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == N
        print(f"\n  cache_like: RSS {rss_before:.0f} -> {rss_after:.0f} MB (delta={rss_after-rss_before:.0f})")
