"""Reproducer: sequential yield of large objects leaks memory in doeff-vm.

When a @do function yields an effect in a loop and the handler returns a large
Python object (~800KB each), RSS grows unboundedly even though user code does
NOT store the returned value. This suggests the VM's continuation chains retain
references to Python objects that should be eligible for GC.

Real-world trigger: a trading signal loop yields HistoricalPrice(request)
~100 times, each returning thousands of price data points. RSS grows ~100MB
every 10 seconds, reaching 7+ GB before OOM.

The leak persists after the #354 fix for shared handler caller chains.

See: https://github.com/proboscis/doeff/issues/XXX
"""

from __future__ import annotations

import gc
import resource
from dataclasses import dataclass

import pytest

import doeff_vm

from doeff import do, run, default_handlers
from doeff.effects.base import EffectBase
from doeff.rust_vm import WithHandler


# ---------------------------------------------------------------------------
# Custom effect: request a chunk of big data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BigDataEffect(EffectBase):
    """Effect requesting a large Python object from a handler."""
    iteration: int


@dataclass(frozen=True)
class TinyEffect(EffectBase):
    """Effect returning a small integer value (control group)."""
    iteration: int


# ---------------------------------------------------------------------------
# Handler: returns ~800KB of floats per Resume
# ---------------------------------------------------------------------------

@do
def big_data_handler(effect: BigDataEffect, k):
    """Handle BigDataEffect by resuming with a large list of floats."""
    # ~800KB: 100_000 floats * 8 bytes each
    big_payload = list(range(100_000))
    return (yield doeff_vm.Resume(k, big_payload))


# ---------------------------------------------------------------------------
# Programs under test
# ---------------------------------------------------------------------------

@do
def _tiny_handler(effect: TinyEffect, k):
    return (yield doeff_vm.Resume(k, effect.iteration))


@do
def _tiny_loop(n: int):
    for i in range(n):
        _ = yield TinyEffect(iteration=i)
        del _
    return n


@do
def _sequential_yield_discard(n: int):
    """Yield BigDataEffect n times, discarding the result each time."""
    for i in range(n):
        _unused = yield BigDataEffect(iteration=i)
        # Explicitly discard – this value should be GC-eligible immediately.
        del _unused
    return n


@do
def _sequential_yield_keep_last(n: int):
    """Yield BigDataEffect n times, keeping only the last result."""
    last = None
    for i in range(n):
        last = yield BigDataEffect(iteration=i)
    return last


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rss_mb() -> float:
    """Current process RSS in megabytes."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # macOS returns bytes; Linux returns kilobytes
    import sys
    if sys.platform == "darwin":
        return usage.ru_maxrss / (1024 * 1024)
    else:
        return usage.ru_maxrss / 1024


def _run_program(program):
    """Run a program with the big_data_handler installed."""
    wrapped = WithHandler(big_data_handler, program)
    return run(wrapped, handlers=default_handlers())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

N_ITERATIONS = 200
# 200 iterations * ~800KB = ~160MB if all retained.
# With proper GC, only one payload (~800KB) should be alive at a time.
# We allow 50MB growth to account for interpreter overhead.
MAX_ALLOWED_GROWTH_MB = 50


class TestVmMemoryLeak:
    @pytest.mark.xfail(
        reason=(
            "TODO(vm-memory): resumed Python handler generators still retain large payloads "
            "across deep Resume chains; re-enable after branch retention is fixed."
        ),
        strict=False,
    )
    def test_sequential_discard_bounded_memory(self):
        """Yielding large objects in a loop without storing them must not leak.

        If the VM retains continuation chain references to yielded values,
        RSS will grow proportionally to N * payload_size (~160MB for N=200).
        A healthy VM should stay well under 50MB growth.
        """
        # Force GC before measurement
        gc.collect()
        rss_before = _rss_mb()

        result = _run_program(_sequential_yield_discard(N_ITERATIONS))

        gc.collect()
        rss_after = _rss_mb()
        delta = rss_after - rss_before

        assert result.is_ok(), f"Program failed: {result.error}"
        assert result.value == N_ITERATIONS

        print(
            f"\n  discard N={N_ITERATIONS}: "
            f"RSS {rss_before:.0f} -> {rss_after:.0f} MB "
            f"(delta={delta:.0f} MB)"
        )
        assert delta < MAX_ALLOWED_GROWTH_MB, (
            f"Memory leak detected! RSS grew by {delta:.0f} MB "
            f"for {N_ITERATIONS} iterations yielding ~800KB each "
            f"(expected <{MAX_ALLOWED_GROWTH_MB} MB). "
            f"The VM continuation chain is likely retaining references "
            f"to yielded effect return values."
        )

    @pytest.mark.xfail(
        reason=(
            "TODO(vm-memory): resumed Python handler generators still retain large payloads "
            "across deep Resume chains; re-enable after branch retention is fixed."
        ),
        strict=False,
    )
    def test_sequential_keep_last_bounded_memory(self):
        """Same as above but keeping a reference to the last result.

        Memory should still be bounded: only the current payload + the last
        one (briefly overlapping) should be alive.
        """
        gc.collect()
        rss_before = _rss_mb()

        result = _run_program(_sequential_yield_keep_last(N_ITERATIONS))

        gc.collect()
        rss_after = _rss_mb()
        delta = rss_after - rss_before

        assert result.is_ok(), f"Program failed: {result.error}"
        # last payload is list(range(100_000))
        assert isinstance(result.value, list)
        assert len(result.value) == 100_000

        print(
            f"\n  keep_last N={N_ITERATIONS}: "
            f"RSS {rss_before:.0f} -> {rss_after:.0f} MB "
            f"(delta={delta:.0f} MB)"
        )
        assert delta < MAX_ALLOWED_GROWTH_MB, (
            f"Memory leak detected! RSS grew by {delta:.0f} MB "
            f"for {N_ITERATIONS} iterations yielding ~800KB each "
            f"(expected <{MAX_ALLOWED_GROWTH_MB} MB). "
            f"The VM continuation chain is likely retaining references "
            f"to yielded effect return values."
        )

    def test_control_small_effects_low_memory(self):
        """Control: same loop pattern but with tiny return values.

        If this test also shows high memory, the leak is in the continuation
        chain structure itself (not the payload). If only the big-payload
        tests fail, the leak is in payload retention.
        """

        wrapped = WithHandler(_tiny_handler, _tiny_loop(N_ITERATIONS))

        gc.collect()
        rss_before = _rss_mb()

        result = run(wrapped, handlers=default_handlers())

        gc.collect()
        rss_after = _rss_mb()
        delta = rss_after - rss_before

        assert result.is_ok(), f"Program failed: {result.error}"
        assert result.value == N_ITERATIONS

        print(
            f"\n  control_tiny N={N_ITERATIONS}: "
            f"RSS {rss_before:.0f} -> {rss_after:.0f} MB "
            f"(delta={delta:.0f} MB)"
        )
        # Tiny effects should use negligible memory
        assert delta < 20, (
            f"Even tiny effects leak {delta:.0f} MB — "
            f"the continuation chain itself is leaking, not just payload."
        )
