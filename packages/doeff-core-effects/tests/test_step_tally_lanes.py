"""The step tally's lanes: windows answer from one running sum per scheduler thread.

- Windows opened at different times each count exactly the steps that started after their opening:
  the step in progress at an opening is left out of that window, and the longest step is the first
  of the longest (the earlier one on a tie) among the steps the window counts.
- The cost of one step does not grow with the number of open windows: with many windows open, a
  task-leave builds no record (until 2026-11-07 every task-leave rebuilt every open window — with 40
  windows open on the agora CLI host one step cost ~760 µs of CPU instead of ~420 µs).

The events are fed to the sink directly with made-up clock values, so the counts are exact.
"""

from __future__ import annotations

import dataclasses
from collections import Counter

import pytest
from doeff_core_effects import _scheduler_step_tally as tally_module
from doeff_core_effects._scheduler import scheduler_trace_sink
from doeff_core_effects._step_tally_effects import SiteTally, StepTally

from doeff import run

THREAD = "lane-test"


def _open(key: str, opened_ns: int) -> None:
    run(tally_module.opened(key, THREAD, opened_ns))


def _close(key: str) -> StepTally | None:
    return run(tally_module.closed(key))


def _leave(started_ns: int, ended_ns: int, site: str, effect: str = "Wait", ready: int = 1) -> None:
    """One task-leave of a step that ran from started_ns to ended_ns (CPU = half the wall time)."""
    wall = ended_ns - started_ns
    tally_module.step_sink({
        "event": "task-leave", "run": 1, "thread": THREAD, "ns": ended_ns, "cpu_ns": 0, "tid": 1,
        "step_ns": wall, "step_cpu_ns": wall // 2, "step_vm_steps": 1, "step_handler_calls": 0,
        "site": site, "effect": effect, "ready": ready,
    })


@pytest.fixture(autouse=True)
def _no_window_left():
    yield
    for key in list(tally_module.WINDOWS):
        _close(key)
    tally_module.LANES.pop(THREAD, None)
    assert scheduler_trace_sink() is None


def test_staggered_windows_count_the_steps_that_started_after_their_opening() -> None:
    _open("first", 100)
    _leave(50, 150, "a")       # in progress when "first" opened: not counted
    _open("second", 155)       # opened while the next step (150 → 160) is in progress
    _leave(150, 160, "b", ready=4)
    _leave(160, 190, "a", effect="Spawn", ready=2)
    _leave(190, 220, "b", effect="Await", ready=3)  # as long as the step before it: the earlier one stays the longest
    second = _close("second")
    _leave(220, 225, "a", ready=5)
    first = _close("first")

    assert second == StepTally(
        steps=2, wall_ns=60, cpu_ns=30, longest_wall_ns=30, longest_cpu_ns=15, longest_site="a",
        longest_effect="Spawn", ready=3,
        sites=(SiteTally(site="a", steps=1, wall_ns=30, cpu_ns=15), SiteTally(site="b", steps=1, wall_ns=30, cpu_ns=15)),
    )
    assert first == StepTally(
        steps=4, wall_ns=75, cpu_ns=37, longest_wall_ns=30, longest_cpu_ns=15, longest_site="a",
        longest_effect="Spawn", ready=5,
        sites=(SiteTally(site="b", steps=2, wall_ns=40, cpu_ns=20), SiteTally(site="a", steps=2, wall_ns=35, cpu_ns=17)),
    )


def test_a_step_of_an_outer_run_that_leaves_after_its_inner_steps_is_counted_by_its_start() -> None:
    _open("outer", 100)
    _leave(50, 120, "x")           # in progress at the opening
    _open("inner", 140)
    _leave(160, 170, "inner")      # steps of a run nested inside the next outer step
    _leave(170, 190, "inner")
    _leave(130, 200, "outer-run")  # the outer step (130 → 200) leaves last: counted by "outer", not by "inner"
    inner = _close("inner")
    outer = _close("outer")

    assert (inner.steps, inner.wall_ns, inner.longest_wall_ns, inner.longest_site) == (2, 30, 20, "inner")
    assert (outer.steps, outer.wall_ns, outer.longest_wall_ns, outer.longest_site) == (3, 100, 70, "outer-run")


def test_a_window_with_no_step_answers_the_empty_tally() -> None:
    _open("busy", 100)
    _leave(100, 400, "a")
    _open("quiet", 450)
    quiet = _close("quiet")
    _close("busy")
    assert quiet == StepTally(steps=0, wall_ns=0, cpu_ns=0, longest_wall_ns=0, longest_cpu_ns=0,
                              longest_site=None, longest_effect=None, ready=0)


def test_a_step_builds_no_record_whatever_the_number_of_open_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    for number in range(64):
        _open(f"w{number}", 100 + number)
    built: Counter[str] = Counter()
    for record in (StepTally, SiteTally, tally_module.StepWindow):
        original = record.__init__

        def counted(self, *args, __original=original, __name=record.__name__, **kwargs):
            built[__name] += 1
            __original(self, *args, **kwargs)

        monkeypatch.setattr(record, "__init__", counted)

    def counted_replace(*args, **kwargs):
        built["dataclasses.replace"] += 1
        return dataclasses.replace(*args, **kwargs)

    monkeypatch.setattr(tally_module.dataclasses, "replace", counted_replace)

    for step in range(1_000):
        _leave(1_000 + step * 10, 1_005 + step * 10, f"site{step % 7}")

    assert built == Counter()
    monkeypatch.undo()
    last = _close("w63")
    assert last.steps == 1_000
    assert sum(row.steps for row in last.sites) == 1_000
