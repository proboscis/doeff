"""The step tally (agora-redesign #3855): OpenStepTally / CloseStepTally answered by step-tally-handler.

- While a window is open, a task that computes raises the window's CPU sum.
- Two overlapping windows both count the steps inside both; a step that began before a window
  opened is not added to that window.
- Steps after a window closed are not added to it: the same key opened again starts from zero.
- Closing a key that is not open answers None.
- No window open → the scheduler has no trace sink.
- Both versions emit the steps (every test runs under each through ``DOEFF_SCHEDULER``): a window
  opened inside the default (Rust) scheduler counts, and the same program counts the same steps
  under both — per window and per spawn site.

These use the real thread clock, so the computation is kept to a few ms.
"""

from __future__ import annotations

import time

import pytest
from doeff_core_effects import (
    CloseStepTally,
    OpenStepTally,
    Spawn,
    StepTally,
    Wait,
    scheduled,
    step_tally_handler,
)
from doeff_core_effects._scheduler import scheduler_trace_sink

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


@pytest.fixture(params=["python", "rust"], autouse=True)
def scheduler_implementation(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run each test under both scheduler versions: both emit the steps the tally counts."""
    monkeypatch.setenv("DOEFF_SCHEDULER", request.param)
    return request.param


def _tallied(body, implementation=None):
    return run(scheduled(with_handlers([step_tally_handler], body), implementation=implementation))


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


def test_window_sums_steps_per_spawn_site() -> None:
    """The window answers per-site sums (sites): one row per task spawn site, CPU-heaviest first, and the rows add up to the window."""

    @do
    def body():
        yield OpenStepTally("sites")
        yield _run_busy()
        return (yield CloseStepTally("sites"))

    tally = _tallied(body())
    assert tally.sites, tally
    assert sum(row.steps for row in tally.sites) == tally.steps, tally
    assert sum(row.wall_ns for row in tally.sites) == tally.wall_ns, tally
    assert sum(row.cpu_ns for row in tally.sites) == tally.cpu_ns, tally
    cpus = [row.cpu_ns for row in tally.sites]
    assert cpus == sorted(cpus, reverse=True), tally.sites
    heaviest = tally.sites[0]
    assert heaviest.cpu_ns >= STEPS * BUSY_CPU_MS * 1_000_000, tally.sites
    assert heaviest.site is not None and "test_step_tally.py" in heaviest.site, heaviest
    assert len({row.site for row in tally.sites}) == len(tally.sites), tally.sites


@do
def _busy_window():
    yield OpenStepTally("busy")
    yield _run_busy()
    return (yield CloseStepTally("busy"))


def test_window_under_the_default_scheduler_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default (Rust) scheduler emits the steps: a window opened inside a plain ``scheduled``
    counts its tasks' work (until 2026-10-18 it closed with none)."""
    monkeypatch.delenv("DOEFF_SCHEDULER", raising=False)
    busy = run(scheduled(with_handlers([step_tally_handler], _busy_window())))
    assert busy.steps > STEPS, busy
    assert busy.cpu_ns >= STEPS * BUSY_CPU_MS * 1_000_000, busy
    assert scheduler_trace_sink() is None


def test_both_versions_count_the_same_steps() -> None:
    """The same program counts the same steps under both versions — in the window and per spawn
    site, and with the same ready count at the last step (the times differ)."""

    @do
    def body():
        yield OpenStepTally("sites")
        yield _run_busy()
        return (yield CloseStepTally("sites"))

    python, rust = _tallied(body(), "python"), _tallied(body(), "rust")

    def counted(tally: StepTally) -> tuple[int, int, dict[str | None, int]]:  # noqa: DOEFF006 — 検の補助の戻り
        return tally.steps, tally.ready, {row.site: row.steps for row in tally.sites}

    assert python.steps > STEPS, python
    assert counted(rust) == counted(python), (rust, python)
