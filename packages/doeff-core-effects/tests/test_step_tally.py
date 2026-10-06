"""The step tally (agora-redesign #3855): OpenStepTally / CloseStepTally answered by step-tally-handler.

- While a window is open, a task that computes raises the window's CPU sum.
- Two overlapping windows both count the steps inside both; a step that began before a window
  opened is not added to that window.
- Steps after a window closed are not added to it: the same key opened again starts from zero.
- Closing a key that is not open answers None.
- No window open → the scheduler has no trace sink.

These use the real thread clock, so the computation is kept to a few ms.
"""

from __future__ import annotations

import time

from doeff_core_effects.scheduler import Spawn, Wait, scheduled, scheduler_trace_sink
from doeff_core_effects.scheduler_step_tally import step_tally_handler
from doeff_core_effects.step_tally_effects import CloseStepTally, OpenStepTally, StepTally

from doeff import do, run, with_handlers

BUSY_CPU_MS = 4
STEPS = 3


def _burn_cpu(ms: float) -> None:
    """Spend ``ms`` of this thread's CPU time (not wall time: a busy machine must not shrink it)."""
    end = time.thread_time_ns() + int(ms * 1_000_000)
    while time.thread_time_ns() < end:
        pass


@do
def _busy():
    """A task that computes a few ms per step."""
    for _ in range(STEPS):
        _burn_cpu(BUSY_CPU_MS)
        task = yield Spawn(_noop())
        yield Wait(task)


@do
def _noop():
    return None
    yield  # a generator: @do takes a generator function


@do
def _run_busy():
    task = yield Spawn(_busy())
    yield Wait(task)


def _tallied(body):
    return run(scheduled(with_handlers([step_tally_handler], body), implementation="python"))


def test_computing_task_raises_the_window_cpu() -> None:
    @do
    def body():
        yield OpenStepTally("quiet")
        quiet = yield CloseStepTally("quiet")
        yield OpenStepTally("busy")
        yield _run_busy()
        busy = yield CloseStepTally("busy")
        return quiet, busy

    quiet, busy = _tallied(body())
    assert isinstance(busy, StepTally)
    assert busy.cpu_ns >= STEPS * BUSY_CPU_MS * 1_000_000, busy
    assert busy.steps > STEPS
    assert busy.longest_cpu_ns >= BUSY_CPU_MS * 1_000_000, busy
    assert busy.longest_effect is not None
    assert quiet == StepTally(steps=0, wall_ns=0, cpu_ns=0, longest_wall_ns=0, longest_cpu_ns=0,
                              longest_site=None, longest_effect=None, ready=0)
    assert scheduler_trace_sink() is None


def test_overlapping_windows_both_count() -> None:
    @do
    def body():
        yield OpenStepTally("outer")
        yield OpenStepTally("inner")
        yield _run_busy()
        inner = yield CloseStepTally("inner")
        outer = yield CloseStepTally("outer")
        return inner, outer

    inner, outer = _tallied(body())
    assert inner.cpu_ns >= STEPS * BUSY_CPU_MS * 1_000_000, inner
    assert outer.cpu_ns >= inner.cpu_ns, (inner, outer)
    assert outer.steps >= inner.steps


def test_step_begun_before_a_window_opened_is_not_added_to_it() -> None:
    @do
    def body():
        yield OpenStepTally("outer")
        task = yield Spawn(_noop())
        yield Wait(task)
        _burn_cpu(BUSY_CPU_MS)
        yield OpenStepTally("late")
        task = yield Spawn(_noop())
        yield Wait(task)
        late = yield CloseStepTally("late")
        outer = yield CloseStepTally("outer")
        return late, outer

    late, outer = _tallied(body())
    assert outer.cpu_ns >= BUSY_CPU_MS * 1_000_000, outer
    assert late.cpu_ns < BUSY_CPU_MS * 1_000_000, late


def test_steps_after_close_are_not_added() -> None:
    @do
    def body():
        yield OpenStepTally("short")
        task = yield Spawn(_noop())
        yield Wait(task)
        short = yield CloseStepTally("short")
        yield _run_busy()
        yield OpenStepTally("short")
        reopened = yield CloseStepTally("short")
        return short, reopened

    short, reopened = _tallied(body())
    assert short.steps > 0, short
    assert short.cpu_ns < BUSY_CPU_MS * 1_000_000, short
    assert reopened.steps == 0, reopened
    assert scheduler_trace_sink() is None


def test_close_of_a_key_never_opened_is_none() -> None:
    @do
    def body():
        return (yield CloseStepTally("never"))

    assert _tallied(body()) is None
    assert scheduler_trace_sink() is None
