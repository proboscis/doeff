"""Handler frame-reclamation contract (ADR-DOE-CORE-EFFECTS-002).

A handler clause must perform its FINAL control instruction (Transfer /
TransferThrow / Resume / Pass / Delegate) from the handler's TOP-LEVEL
frame. If the final instruction executes inside a nested ``yield
sub_program(...)`` delegation, every ancestor frame still suspended
mid-``yield`` is abandoned by the VM but never reclaimed — not even by a
full ``gc.collect()``. The parked frames' locals pin the Task/continuation
handles, the handles' weakrefs never die, and the scheduler's
terminal-entry sweep (``sweep_terminal_unobserved_entries``) can never
reclaim the completed tasks' entries.

Under a daemon beat loop (Spawn x2 + Await bridge + Gather + Delay) the
pre-fix nested doeff-time handler accumulated +5 suspended frames and +2
terminal task entries PER BEAT, growing GC cost linearly with wall time
(live symptom: detect-to-submit latency 2.9ms -> 79ms over 4.5h;
proboscis-ema ISSUE-TRD-185, 2026-07-13/14 forensics).

Secondary rule pinned here indirectly: a flat handler frame parked at a
tail ``Resume`` IS collectable by cycle GC (observed 2026-07-14), but
``Transfer`` releases it deterministically via refcount without waiting
for a GC pass — hot-path handlers must use Transfer in tail position
(defhandler's TCO already does this for Hy handlers).

Two tests pin both directions:
- the stock handler stack stays FLAT over the beat loop;
- a deliberately nested-delegating time handler (the pre-fix doeff-time
  shape) leaks, proving the census methodology detects the regression.
"""

from __future__ import annotations

import asyncio
import gc
from datetime import datetime, timezone

from doeff_core_effects.handlers import (
    await_handler,
    lazy_ask,
    state,
    try_handler,
)
from doeff_core_effects.scheduler import scheduled
from doeff_time import Delay
from doeff_time.effects import DelayEffect, GetTimeEffect
from doeff_time.handlers.async_time import async_time_handler

from doeff import Await, Gather, Spawn, do, run
from doeff import handler as _program_handler
from doeff.program import Pass, Transfer

N_WARM = 100
N_TOTAL = 400
BEAT_SECONDS = 0.0005
# Stock stack: growth over 300 beats must be noise-level (a mid-delegation
# regression in ANY handler on the stack adds >= +1/beat = +300).
FLAT_LIMIT = 60
# Control (nested delegating time handler): the wrapper frame parks on
# every invocation whose final instruction runs in a deeper frame —
# >= 1/beat from the Delay path alone.
LEAK_FLOOR = 200


def _suspended_generator_count() -> int:
    """Count live suspended generator frames process-wide."""
    gc.collect()
    return sum(
        1
        for o in gc.get_objects()
        if type(o).__name__ == "generator" and o.gi_frame is not None
    )


@do
def _keepalive():
    yield Await(asyncio.sleep(0.0001))
    return 1


def _beat_loop(census: list):
    """N_TOTAL beats of Spawn x2 + Gather + Delay, with a generator census
    taken from INSIDE the run at beat N_WARM and at the last beat (leaked
    frames may become collectable once run() tears down, so measuring
    across runs would miss the in-run growth that inflates GC cost)."""

    @do
    def loop():
        for i in range(N_TOTAL):
            t1 = yield Spawn(_keepalive())
            t2 = yield Spawn(_keepalive())
            yield Gather(t1, t2)
            yield Delay(BEAT_SECONDS)
            if (i + 1) in (N_WARM, N_TOTAL):
                census.append(_suspended_generator_count())
        return N_TOTAL

    return loop()


def _nested_delegating_time_handler():
    """The PRE-FIX doeff-time shape, kept as the ADR counterexample: a
    wrapper @do delegates to a sub-@do dispatcher, whose leaf performs the
    final Transfer/Pass. Both delegating ancestor frames are suspended
    mid-``yield`` at that moment and are retained forever."""

    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @do
    def _leaf_delay(effect, k):
        yield Await(asyncio.sleep(max(0.0, effect.seconds)))
        return (yield Transfer(k, None))

    @do
    def _dispatch(effect, k):
        if isinstance(effect, DelayEffect):
            return (yield _leaf_delay(effect, k))  # deliberate: nested final Transfer
        if isinstance(effect, GetTimeEffect):
            return (yield Transfer(k, _utc_now()))
        yield Pass(effect, k)

    @do
    def handler(effect, k):
        return (yield _dispatch(effect, k))  # deliberate: mid-delegation wrapper

    return _program_handler(handler)


def _run_beat_loop(time_installer) -> int:
    census: list = []
    composed = lazy_ask(env={})(
        state()(
            try_handler(
                await_handler()(
                    time_installer(
                        _beat_loop(census)
                    )
                )
            )
        )
    )
    result = run(scheduled(composed))
    assert result == N_TOTAL
    assert len(census) == 2
    return census[1] - census[0]


def test_stock_handler_stack_does_not_accumulate_suspended_frames():
    growth = _run_beat_loop(async_time_handler())
    assert growth <= FLAT_LIMIT, (
        f"suspended handler generators grew by {growth} over "
        f"{N_TOTAL - N_WARM} beats — a handler on the stock stack is "
        "performing its final Resume/Transfer/Pass from a nested sub-@do "
        "frame (mid-delegation ancestors park forever) instead of its "
        "top-level frame (ADR-DOE-CORE-EFFECTS-002)"
    )


def test_nested_delegating_handler_leaks_suspended_frames():
    growth = _run_beat_loop(_nested_delegating_time_handler())
    assert growth >= LEAK_FLOOR, (
        f"expected the deliberate nested-delegation handler to park >= "
        f"{LEAK_FLOOR} frames over {N_TOTAL - N_WARM} beats but it grew by "
        f"{growth} — if the VM now reclaims abandoned mid-delegation "
        "handler frames, revisit ADR-DOE-CORE-EFFECTS-002 (the flat-frame "
        "law may be obsolete) and update both tests together"
    )
