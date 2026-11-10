"""Time budgets for Hy tests (running one test, collecting one Hy test file).

Tests run on mock handlers are expected to be fast, so this makes a test that has become slow noticeable.
Settings to put in the consumer repo's ``[tool.pytest.ini_options]``:

- ``doeff_test_call_budget_steps`` — upper bound, in doeff-vm steps, on running one test collected from a Hy test file (the pytest call phase)
  (a positive integer — a fixed number that does not wobble with machine load). This is the core of the run judgment: it judges by steps, and CPU seconds are only measured and shown in the report.
  If a build has no counter hook in doeff-vm, stop with ``pytest.UsageError`` instead of falling back to seconds.
- ``doeff_test_call_budget_by_marker`` — per-marker run budget in wall-clock seconds (one line = ``marker=seconds``, e.g. ``real_world=10``). A line that matches a marker the test has
  (``item.iter_markers()`` — including a module-level ``pytestmark``) gives that many seconds; if several match, the longest wins
  (a test with real I/O shows no weight in steps, so it stays in seconds even when a step budget exists). A test matching no line is judged by the step budget, and
  if there is no step budget either, its run is not measured. The run-seconds budget exists only per marker (the default seconds for unmarked tests, ``doeff_test_call_budget_seconds``, was
  removed — the 2026-10-04 decision "no backward compatibility needed". Writing it only makes pytest warn about an unregistered setting; runs are not judged by seconds).
  The collect budget is per file and has no marker, so it is a single value.
- ``doeff_test_collect_budget_seconds`` — CPU-seconds budget for importing the module of one Hy test file.
- ``doeff_test_budget_mode`` — ``report`` (default: exceeding does not turn red; only a warning and the final list) or ``fail`` (exceeding turns red).
- ``doeff_test_budget_registry`` — dir of the registry of existing tests allowed to exceed the budget (relative to rootdir; one line = one dir;
  a single-value form is also read as is). A key listed in any dir is never turned red. The over-budget message names the first dir as where to add.

- Step-budget details: the registry is the same dir as the seconds judgment (a listed line is judged by steps if the test is judged by steps). A test with 0 steps (it only reads a tree without running the VM) is within budget.
  For a Hy test judged by steps, only during the call, a step cap (this value × ``step_budget_multiplier`` (default ``DEFAULT_RUNAWAY_STEPS_MULTIPLIER`` = 20)) is set on the doeff scheduler
  (#3143). A spin that keeps waking tasks at the same virtual time never finishes, so it never reaches the post-run judgment — when it tries to
  wake a task past the cap, the scheduler raises ``StepBudgetExceeded`` and that test turns red on the spot. It is not set in a repo with no setting or
  with a scheduler version that has no hook for it (same as before).
- Per-test declaration ``declare_step_budget(test, steps, reason)`` — the default budget is unchanged; only a test whose assertions depend on the scale of its scenario and so cannot be shrunk
  declares the steps it needs and a one-line reason next to the test definition (decision of the Mac coordinator, 2026-10-03 21:5x — in place of a
  registry in a separate file). When judging by steps, a test with a declaration is judged by the declared value: exceeding it is red; finishing at or below `STALE_RATIO` (half) of the declaration
  is red as a stale declaration. A test without a declaration is judged by the default budget (and the registry) as before.
- Judge only by the steps of the simulated world's run (decision of coordinator cisco-c8, 2026-10-12 15:47 JST — revising the #2670 counting):
  doeff-cluster's ``sim-main`` leaves marks for begin, start-up (the first time the world goes quiet after all declared jobs have woken), start of stopping, and end
  in ``doeff_core_effects._world_phases``. In a repo with ``doeff_test_call_steps_counted = "world-running"`` (the default ``call`` is
  all of the call as before), the record is opened during the call of a test judged by steps, and for a test that ran a world, only the steps from start-up to
  the start of stopping are compared with the budget (declaration). Steps for start, stop, and outside the world (setup and verdict) are not counted and are shown as separate values in the report message
  (so the ever-growing start/stop cost of adding components does not keep raising the budget, and sensitivity to spins is kept). A test that does not run a world is judged by
  all of the call as before. The in-flight step cap (runaway detection) stays on the whole call's steps. It is a setting so that a repo can do the switch of counting
  and the re-measurement of declarations in one change (so the consumer's judgment does not change even if doeff lands first), and so that rolling back is
  one setting line (rollback = set it back to ``call`` and re-measure declarations).

- Python call counts (``doeff_hy_pytest._python_calls``): only in runs with the command-line flag ``--doeff-python-calls``,
  count ``sys.monitoring`` CALL events during the call of tests that declare steps, and print one line at the end (no judgment). A run without the flag takes no slot.

Does nothing unless at least one of the three budgets (steps, per-marker run seconds, collect seconds) is set. Design decisions:

- Only ``.hy`` test files are covered (deftest and plain tests in the same file). Python tests are not necessarily mock-handler tests, so they are excluded.
- A run that hits a marker budget is judged by wall-clock seconds (``time.perf_counter`` — the span of the call phase), and CPU seconds are shown alongside in the report. Marked tests
  (real_world etc.) deal with real child processes, sockets, and listeners, so the weight is in child processes and waiting and does not show in the process CPU seconds
  (``time.process_time`` — only all threads of its own process) (a real_world test that took 105 seconds under a limit of
  60 seconds did not turn red on CPU seconds). Wall-clock stretches with machine load (other tests and seats running side by side),
  so the daily Pod's seconds come out larger than local ones.
- Per-test declaration ``declare_process_tree_cpu(test, reason)`` — among tests that hit a marker budget, only a test whose weight is in the CPU of child processes (and its own threads) and
  that does not wait on the outside (e.g. a test that fires a type-checking tool in a child) declares that it is judged, instead of by wall-clock, by the CPU seconds of the process tree (own plus descendants, user+sys —
  ``doeff_hy_pytest._tree_cpu``) against the same seconds budget. Wall-clock stretches with machine congestion
  (CPU wait and disk wait), so the same test turns red only in congested runs — in the daily run of 2026-10-22 04:00 JST, a test that fires a type check in a child exceeded the 30-second limit with
  wall-clock 33 seconds and own CPU 0.96 seconds, and was green with the limit removed. Tree CPU seconds are the amount of work the test did and
  barely change with congestion. Wall-clock retreats to a safety valve that cuts a test that stopped without using CPU (budget × ``TREE_CPU_WALL_BACKSTOP_FACTOR``)
  (the same shape as making the agora-controllers whole-repo linter budget CPU seconds — 9c1a6659f7). On a machine that cannot read tree CPU seconds (macOS, with no ``/proc``),
  a declared test is also judged by the wall-clock budget (no looser than before). Tests that wait on the outside (socket, HTTP, listener) do not declare —
  waiting does not show in CPU seconds.
- The collect judgment stays on CPU time (``time.process_time``), with wall-clock shown alongside. Import is assumed to have no child processes or waiting,
  so slowness shows almost entirely in CPU time, and CPU time is less likely to flip pass/fail with machine congestion.
- Run measures only the call phase and excludes setup and teardown (fixtures). The preparation of a session- or module-scope fixture
  falls entirely on the first test that runs in that scope, so including it would change the same test's pass/fail with test selection and order. The body of deftest
  (where a Program is run with mock handlers) is in the call phase.
- The file budget measures the module import, and the first import of another module (a one-time cost of shared dependencies) is subtracted, like compilation
  (2026-09-30, revised — before, dependency imports were included, and since that weight lands on the first file imported in the process,
  the same file's pass/fail changed with order, whether only one file was run, and whether the test index was cold. Rollback =
  remove the replacement of ``_find_and_load`` in ``CompileCounter.install``). The measured span is before and after doeff-adr's hook
  ``pytest_doeff_import_hy_module`` — doeff-adr collects files explainable from the record without importing them, and defers the module import
  to the item's setup (#1225), so the import happens once, either during collection or during setup.
  Either way it passes through the same hook, so it is measured under the same key (the file path). When it exceeds and turns red, it fails on the spot (a collection
  error if in collection, a setup error of that item if in setup).
- Do not turn a missing cache red: from the measured span, the time spent converting source to code (``SourceFileLoader.source_to_code`` —
  called only when the bytecode cache missed; Hy imports go through the same hook that Hy replaced) is subtracted, on the same clock as the judged unit
  (CPU time for collect, wall-clock for per-marker runs), and the result is judged (for nested conversions only the outermost span is subtracted). The whole run is not excluded because the landing gate and the daily
  run fire with `PYTHONDONTWRITEBYTECODE=1` and the cache is never created — the exclusion approach would never judge there.
  With a cache, the subtracted amount is 0 and the value is unchanged. The no-cache value is a lower bound (the execution of modules that Hy reads for ``require`` during
  conversion is also subtracted), so anything over the budget at the lower bound truly exceeds it.
- GC (garbage collection) is also subtracted: GC time that occurred inside the measured span (``gc.callbacks`` — CPU seconds or wall-clock seconds, the same clock as the judged unit) is not that test's weight but a cost of the number of
  live objects in the process, and when it happens is decided by the cycle of allocation counts (the free-threaded Python GC does not split generations and walks the whole heap
  every time). Without subtracting, only the test that happens to hit GC in a long session turns red, and the failing tests change from run to run.
- The registry is one file per key (``<first 12 chars of the key's sha256>.txt``; line 1 is the key, line 2 onward is the reason, and an empty reason is not allowed). The key is
  the pytest nodeid for runs, and the file path from rootdir for collection. A listed key does not turn red even over the budget. A key back within the budget
  is reported as "removable" in the final summary (the registry only shrinks — turning an increase red is the consumer repo's git test).
  In ``fail`` mode, when a listed key finishes at or below ``STALE_RATIO`` (half) of the budget, that test is turned red as a stale registration
  (so a forgotten line is not silently passed when the same test comes back slow; same direction as doeff-linter's DOEFF166).
  Between half the budget and the budget it is report-only (so the same test's pass/fail does not change from run to run with jitter in seconds).
  Only measured tests are judged (tests that did not run, or keys of other checks in the same dir, are not judged).
- The budget is based on runs where doeff-vm's invariant checks are disabled. Runs with checks enabled (doeff's own pytest — enabled by the root
  conftest.py; every build now has the checks and switches them at runtime) check invariants at every VM
  step, and the same test is ten-odd times slower. At session start the doeff effect ``ReadInvariantChecks`` is read once, and
  with checks on, even ``fail`` does not turn red and stays in the report (so a build with a different baseline does not produce a false red). The heading of the final summary shows
  the build kind in one line. When it cannot be read (cannot import, or an old VM cannot answer) it shows "unknown" and the judgment stays as is.
"""

import contextlib
import gc
import hashlib
import importlib.machinery
import itertools
import re
import statistics
import sys
import time
import types
import warnings
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import pytest

from doeff_hy_pytest._python_calls import (
    PYTHON_CALLS_DEST,
    PYTHON_CALLS_OPTION,
    CallCounter,
    CallCounting,
    CalledTest,
    NotCounted,
    called_test_line,
    summary_line,
    unmeasurable_line,
)
from doeff_hy_pytest._tree_cpu import TreeCpu, TreeCpuSampler, TreeCpuUnmeasured

MARKER_CALL_BUDGET_INI = "doeff_test_call_budget_by_marker"
COLLECT_BUDGET_INI = "doeff_test_collect_budget_seconds"
MODE_INI = "doeff_test_budget_mode"
REGISTRY_INI = "doeff_test_budget_registry"
CALL_STEPS_INI = "doeff_test_call_budget_steps"
RUNAWAY_MULTIPLIER_INI = "step_budget_multiplier"
# Counting span used when judging by steps: call = all of the call (default); world-running = for a test that ran a simulated world, only the steps of the world's run.
# A repo chooses it by setting — do the switch in the same change as re-measuring that repo's declarations, and revert this one line to roll back.
STEPS_COUNTED_INI = "doeff_test_call_steps_counted"
StepsCounted = Literal["call", "world-running"]
# Default step budget (when `default` is written in the setting) — the number corresponding to the former CPU 1.0 second budget.
# Root = the median 95,574 steps/second of steps / CPU seconds from the 65 measurements on vg-w43 (#2853 issuecomment-5952699812) × 1.0 second, rounded.
# The decision is not to raise the budget (#2670 issuecomment-5963008183), so we take this ratio rather than the (larger) ratio of a quiet machine.
DEFAULT_CALL_BUDGET_STEPS = 100_000
# Multiplier for the in-flight step cap (#3143) — a test exceeding the step budget × this value is dropped by the doeff scheduler with a
# named exception without waiting for it to finish (a spin at the same virtual time never finishes, and the 60-second per-test limit cannot stop the VM step loop either).
# Large to keep it separate from the post-run step judgment (the budget itself). Default is 20 — a heavy registered test (13.4x the budget = 2,282,746 steps,
# #2670 issuecomment-5967500669) turned red at 10x. A spin reaches billions of steps in 30 seconds, so even 20x catches it in 1-2 seconds.
# A repo can change it with ini step_budget_multiplier (#3147).
DEFAULT_RUNAWAY_STEPS_MULTIPLIER = 20
# Judgment unit (budget, registration, and message wording differ by unit) — collect budget is CPU seconds; per-marker run budget is wall-clock seconds (for a test declaring tree CPU seconds,
# process-tree CPU seconds); unmarked runs are steps.
Unit = Literal["cpu-seconds", "wall-seconds", "tree-cpu-seconds", "steps"]

Phase = Literal["call", "collect"]
# Kinds of span subtracted from a measurement (CompileCounter).
SetAside = Literal["compile", "import"]
# The hook through which the first import of another module passes (looked up by name on every call of the import machinery — CompileCounter replaces it to count).
_BOOTSTRAP_MODULE = "importlib._bootstrap"
_FIND_AND_LOAD = "_find_and_load"
Mode = Literal["report", "fail"]
# Fraction of seconds at or below which a registered test is turned red as a stale registration (budget × this value — so it does not flip between red and green with jitter near the budget).
# The staleness of a test declaration (StepDeclaration) is judged by the same fraction.
STALE_RATIO = 0.5
# Attribute of the "needed steps" declaration set on a test function — set by declare_step_budget, read by the run judgment.
DECLARED_STEPS_ATTRIBUTE = "__doeff_step_budget__"
# Attribute of the "judge by process-tree CPU seconds" declaration set on a test function — set by declare_process_tree_cpu, read by the run judgment.
DECLARED_TREE_CPU_ATTRIBUTE = "__doeff_process_tree_cpu__"
# Multiplier of the wall-clock safety valve for a test judged by tree CPU seconds — a test exceeding marker seconds × this value in wall-clock is red even if CPU seconds are within budget (so a test that stopped or waited
# without using CPU is not let through). 2 = same as the safety valve of the agora-controllers whole-repo linter (WALL-BACKSTOP-FACTOR of 9c1a6659f7).
TREE_CPU_WALL_BACKSTOP_FACTOR = 2


@dataclass(frozen=True)
class CheckedVmBuild:
    """doeff-vm is a build with invariant-checks (``make sync``) — different from the budget baseline, so overruns are not judged."""


@dataclass(frozen=True)
class UncheckedVmBuild:
    """doeff-vm is a build without checks — the baseline build for the budget."""


@dataclass(frozen=True)
class UnknownVmBuild:
    """Could not read the doeff-vm build kind (with a reason) — judged the same as the no-checks case."""

    reason: str


VmBuild = CheckedVmBuild | UncheckedVmBuild | UnknownVmBuild


def read_vm_build() -> VmBuild:
    """Read the doeff-vm build kind with the doeff effect ``ReadInvariantChecks`` (once at session start)."""
    try:
        from doeff_core_effects import vm_probe_handler

        from doeff import ReadInvariantChecks, run
    except ImportError as exc:
        return UnknownVmBuild(f"cannot import ReadInvariantChecks from doeff (doeff older than the effect, or no doeff): {exc}")
    try:
        enabled = run(vm_probe_handler(ReadInvariantChecks()))
    except ImportError as exc:  # vm_probe_handler refused by name a doeff-vm extension older than doeff
        return UnknownVmBuild(str(exc))
    return CheckedVmBuild() if enabled else UncheckedVmBuild()


@dataclass(frozen=True)
class VmWork:
    """Steps all doeff-vm in the process have advanced up to some point, and the number of handler calls (cumulative — does not wobble with machine load)."""

    steps: int
    handler_calls: int

    def since(self, before: "VmWork") -> "VmWork":
        """Difference from before to this point — the amount of work inside one measured span."""
        return VmWork(self.steps - before.steps, self.handler_calls - before.handler_calls)


@dataclass(frozen=True)
class WorkReader:
    """Hook that reads doeff-vm's cumulative counts (runs the doeff effect ``ReadVmWorkCounts`` once and reads it).
    Reading is itself a run, so the difference between the two reads before and after a span contains a fixed number of steps (and one handler call) of the reading run."""

    read: Callable[[], tuple[int, int]]

    def tally(self) -> VmWork:
        """Current cumulative counts — read before and after a span, and the difference is the span's amount of work."""
        steps, handler_calls = self.read()
        return VmWork(steps, handler_calls)


@dataclass(frozen=True)
class NoWorkReader:
    """doeff-vm has no cumulative-count hook (with a reason — old build, cannot import). Counts cannot be measured, so stop if a step budget exists."""

    reason: str


WorkSource = WorkReader | NoWorkReader


def read_work_source() -> WorkSource:
    """Look up the cumulative-count hook once at session start: the doeff effect ``ReadVmWorkCounts`` and the ``vm_probe_handler`` that answers it.
    Try reading once; if the doeff-vm extension is too old to answer (the handler refuses with ImportError), there is no hook."""
    try:
        from doeff_core_effects import vm_probe_handler

        from doeff import ReadVmWorkCounts, run
    except ImportError as exc:
        return NoWorkReader(f"cannot import ReadVmWorkCounts from doeff (doeff older than the effect, or no doeff): {exc}")

    def read() -> tuple[int, int]:
        """Read the cumulative counts (steps, handler calls) of all VMs in the process before and after a test span."""
        counts: tuple[int, int] = run(vm_probe_handler(ReadVmWorkCounts()))
        return counts

    try:
        read()
    except ImportError as exc:  # vm_probe_handler refused by name a doeff-vm extension older than doeff
        return NoWorkReader(str(exc))
    return WorkReader(read)


@dataclass(frozen=True)
class SchedulerStepGuard:
    """Hook for the doeff scheduler's step cap (``arm_step_budget`` /
    ``disarm_step_budget`` in ``doeff_core_effects._scheduler`` — #3143). While armed, a run that tries to wake a task past the cap is dropped with ``StepBudgetExceeded``."""

    arm: Callable[[int], object]
    disarm: Callable[[], None]


@dataclass(frozen=True)
class NoSchedulerStepGuard:
    """The scheduler has no step-cap hook (with a reason — cannot import, or a version older than the hook). The in-flight cap is not armed."""

    reason: str


StepGuard = SchedulerStepGuard | NoSchedulerStepGuard


def read_step_guard() -> StepGuard:
    """Look up the scheduler step-cap hook once at session start (this plugin does not depend on doeff, so if absent it simply does not arm)."""
    try:
        import doeff_core_effects._scheduler as scheduler
    except ImportError as exc:
        return NoSchedulerStepGuard(f"cannot import doeff_core_effects._scheduler: {exc}")
    arm = getattr(scheduler, "arm_step_budget", None)
    disarm = getattr(scheduler, "disarm_step_budget", None)
    if arm is None or disarm is None:
        return NoSchedulerStepGuard("doeff_core_effects._scheduler has no arm_step_budget (version older than the step-cap hook)")
    return SchedulerStepGuard(arm, disarm)


class PhaseMarkRead(Protocol):
    """Shape of one mark returned by the phase-mark hook (``doeff_core_effects._world_phases.PhaseMark`` — this plugin does not depend on doeff, so it reads only the shape)."""

    @property
    def phase(self) -> str: ...

    @property
    def steps(self) -> int: ...


@dataclass(frozen=True)
class WorldPhaseLog:
    """Hook for the simulated world's phase marks (``open_phase_log`` / ``close_phase_log`` in ``doeff_core_effects._world_phases``).
    While open, doeff-cluster's ``sim-main`` records marks for begin, start-up, start of stopping, and end together with the step count at that time."""

    open: Callable[[], None]
    close: Callable[[], tuple[PhaseMarkRead, ...]]


@dataclass(frozen=True)
class NoWorldPhaseLog:
    """No phase-mark hook (with a reason — cannot import, or a version older than the hook). Steps are judged by all of the call (as before)."""

    reason: str


PhaseLogSource = WorldPhaseLog | NoWorldPhaseLog


def read_phase_log() -> PhaseLogSource:
    """Look up the phase-mark hook once at session start (this plugin does not depend on doeff, so if absent it simply judges by all of the call)."""
    try:
        from doeff_core_effects import _world_phases as world_phases
    except ImportError as exc:
        return NoWorldPhaseLog(f"cannot import doeff_core_effects._world_phases: {exc}")
    return WorldPhaseLog(world_phases.open_phase_log, world_phases.close_phase_log)


@dataclass(frozen=True)
class WorldSteps:
    """The call's steps split by events of the simulated world. running = from start-up to start of stopping (the judged value);
    starting = from begin to start-up (for a world that never started up, until start of stopping); stopping = from start of stopping to end; outside =
    outside the world (scenario setup and answer verification). worlds = number of worlds begun; up = number of worlds that started up."""

    outside: int
    starting: int
    running: int
    stopping: int
    worlds: int
    up: int


@dataclass(frozen=True)
class WorldMark:
    """One world mark (phase name — begun, up, stopping, ended) and the process's cumulative step count at that time."""

    phase: str
    steps: int


Segment = Literal["outside", "starting", "running", "stopping"]


def segment_after(mark: WorldMark, segment: Segment) -> Segment:
    """To decide the span after a mark — start-up is delimited only inside the startup span (a start-up that arrives after stopping began is not delimited)."""
    match mark.phase:
        case "begun":
            return "starting"
        case "up" if segment == "starting":
            return "running"
        case "stopping":
            return "stopping"
        case "ended":
            return "outside"
        case _:
            return segment


def world_steps(marks: Sequence[WorldMark], started_at: int, ended_at: int) -> WorldSteps | None:
    """Split the steps from call start started_at to call end ended_at into spans by world marks (in order of occurrence). A test where no world
    began (a test that does not run a simulated world) gives None — judged by all of the call."""
    if not any(mark.phase == "begun" for mark in marks):
        return None
    # segments[i] = the i-th span (i = 0 is from call start to the first mark; the last is from the last mark to call end).
    segments = tuple(itertools.accumulate(marks, lambda segment, mark: segment_after(mark, segment), initial="outside"))
    bounds = (started_at, *(mark.steps for mark in marks), ended_at)
    spans = tuple(zip(segments, (end - start for start, end in itertools.pairwise(bounds)), strict=True))
    return WorldSteps(
        outside=sum(steps for segment, steps in spans if segment == "outside"),
        starting=sum(steps for segment, steps in spans if segment == "starting"),
        running=sum(steps for segment, steps in spans if segment == "running"),
        stopping=sum(steps for segment, steps in spans if segment == "stopping"),
        worlds=sum(1 for mark in marks if mark.phase == "begun"),
        up=sum(1 for mark, segment in zip(marks, segments, strict=False) if mark.phase == "up" and segment == "starting"),
    )


def vm_build_line(build: VmBuild) -> str:
    """One line of the build kind shown in the heading of the final summary."""
    match build:
        case CheckedVmBuild():
            return (
                "doeff-vm is a build with checks (invariant-checks) — the budget is based on a build without checks, so "
                "overruns in this run are not judged"
            )
        case UncheckedVmBuild():
            return "doeff-vm is a build without checks — judged on the budget baseline build"
        case UnknownVmBuild(reason=reason):
            return f"doeff-vm build kind unknown ({reason}) — judged as configured"


@dataclass(frozen=True)
class Budgets:
    """Budgets read from settings (collect seconds; steps — None = that phase is not measured), per-marker run-seconds budgets, what to do on overrun,
    the registry (key -> reason over all dirs), the registry dirs (in setting order), and the doeff-vm build kind. When a step budget exists, ``pytest_configure`` guarantees the count hook
    exists (``work_source`` is a ``WorkReader``)."""

    call_seconds_by_marker: Mapping[str, float]
    collect_seconds: float | None
    mode: Mode
    registry: Mapping[str, str]
    registry_dirs: tuple[str, ...]
    vm_build: VmBuild
    call_steps: int | None = None
    work_source: WorkSource = NoWorkReader("not measured")
    step_guard: StepGuard = NoSchedulerStepGuard("not looked up")
    runaway_multiplier: int = DEFAULT_RUNAWAY_STEPS_MULTIPLIER
    phase_log: PhaseLogSource = NoWorldPhaseLog("not looked up")

    def runaway_steps_for(self, markers: Sequence[str], declared_steps: int | None = None) -> int | None:
        """The step cap to arm on the scheduler during that test's call (#3143) — only for a test judged by steps: the larger of the step budget (the declared value if declared) and
        the default, × the multiplier. A test hitting a per-marker seconds budget (real I/O) is judged by seconds, so it is not armed."""
        if self.call_steps is None:
            return None
        if any(name in self.call_seconds_by_marker for name in markers):
            return None
        return max(self.call_steps, declared_steps or 0) * self.runaway_multiplier

    @property
    def fails_over_budget(self) -> bool:
        """Whether a seconds overrun turns red — only in fail mode and when doeff-vm is not a build with checks."""
        return self.mode == "fail" and not isinstance(self.vm_build, CheckedVmBuild)

    def fails_for(self, unit: Unit) -> bool:
        """Whether an overrun of that unit turns red. Steps do not change with invariant checks (checks run inside a step and add no steps),
        so in fail mode it turns red even on a build with checks — doeff's own runs, which could not be judged by seconds, can be judged by steps."""
        match unit:
            case "cpu-seconds" | "wall-seconds" | "tree-cpu-seconds":
                return self.fails_over_budget
            case "steps":
                return self.mode == "fail"


@dataclass(frozen=True)
class CompileTally:
    """Number of source-to-code conversions counted up to some point, with the CPU seconds and wall-clock seconds spent on them (sum over outermost spans)."""

    count: int
    cpu_seconds: float
    wall_seconds: float

    def since(self, before: "CompileTally") -> "CompileTally":
        """Difference from before to this point — conversions that happened inside one measured span."""
        return CompileTally(
            self.count - before.count,
            self.cpu_seconds - before.cpu_seconds,
            self.wall_seconds - before.wall_seconds,
        )


@dataclass(frozen=True)
class ImportTally:
    """Number of first imports of other modules counted up to some point, with the CPU seconds and wall-clock seconds spent on them (sum over outermost spans)."""

    count: int
    cpu_seconds: float
    wall_seconds: float

    def since(self, before: "ImportTally") -> "ImportTally":
        """Difference from before to this point — first dependency imports that happened inside one measured span."""
        return ImportTally(
            self.count - before.count,
            self.cpu_seconds - before.cpu_seconds,
            self.wall_seconds - before.wall_seconds,
        )


NO_IMPORTS = ImportTally(0, 0.0, 0.0)


@dataclass(frozen=True)
class GcTally:
    """Number of GC (garbage collection) runs counted up to some point, with the CPU seconds and wall-clock seconds spent on them (only the part that
    happened outside the conversion and first-dependency-import spans)."""

    count: int
    cpu_seconds: float
    wall_seconds: float

    def since(self, before: "GcTally") -> "GcTally":
        """Difference from before to this point — GC that happened inside one measured span."""
        return GcTally(
            self.count - before.count,
            self.cpu_seconds - before.cpu_seconds,
            self.wall_seconds - before.wall_seconds,
        )


NO_GC = GcTally(0, 0.0, 0.0)


@dataclass(frozen=True)
class Measurement:
    """One measured span — CPU seconds and wall-clock seconds (both for the whole span), and the conversions, first dependency imports, and GC inside the span."""

    key: str
    phase: Phase
    cpu_seconds: float
    wall_seconds: float
    compile: CompileTally
    imports: ImportTally = NO_IMPORTS
    # Amount of doeff-vm work in the span (None when there is no count hook, or in the collect phase).
    work: VmWork | None = None
    gc: GcTally = NO_GC
    # The span's steps split by simulated-world events (None for a test that does not run a world, or in the collect phase).
    world: WorldSteps | None = None
    # Number of Python calls in the span (sys.monitoring CALL events — only for tests that declared steps in a run with the --doeff-python-calls flag; None otherwise).
    # The CPU seconds of a counted test include monitoring overhead (1.5-1.7x), so they are excluded from the CPU comparison (CPU seconds / steps).
    python_calls: int | None = None
    # Process-tree CPU seconds of the span (only for tests declared to be judged by tree CPU seconds — None for other tests and collection. On a machine that cannot read it: the reason it could not be measured).
    tree: TreeCpu | TreeCpuUnmeasured | None = None

    @property
    def judged_steps(self) -> int:
        """Value when judging by steps = for a test that ran a world, the steps of the world's run (start and stop not counted); otherwise
        the span's steps."""
        if self.work is None:
            raise AssertionError(f"a measurement without steps was judged by steps: {self.key}")
        return self.world.running if self.world is not None else self.work.steps

    @property
    def judged_cpu_seconds(self) -> float:
        """Value when judging by CPU seconds = the span's CPU seconds minus the conversion CPU seconds, first-dependency-import CPU seconds, and GC CPU seconds (if none of them,
        the span's CPU seconds as is)."""
        return self.cpu_seconds - self.compile.cpu_seconds - self.imports.cpu_seconds - self.gc.cpu_seconds

    @property
    def judged_wall_seconds(self) -> float:
        """Value when judging by wall-clock seconds = the span's wall-clock seconds minus the wall-clock seconds of conversion, first dependency imports, and GC (time of child processes and
        waiting remains — the weight of a marked test is there)."""
        return self.wall_seconds - self.compile.wall_seconds - self.imports.wall_seconds - self.gc.wall_seconds

    @property
    def judged_tree_cpu_seconds(self) -> float:
        """Value when judging by tree CPU seconds = the sum of own and descendants' CPU seconds minus the CPU seconds of conversion, first dependency imports, and GC (all of them own-process amounts).
        A measurement that did not measure the tree is not judged by tree CPU seconds."""
        if not isinstance(self.tree, TreeCpu):
            raise AssertionError(f"a measurement without tree CPU seconds was judged by tree CPU seconds: {self.key}")
        return self.tree.seconds - self.compile.cpu_seconds - self.imports.cpu_seconds - self.gc.cpu_seconds


@dataclass(frozen=True)
class WithinBudget:
    measurement: Measurement


def judged_value(measurement: Measurement, unit: Unit) -> float:
    """Measurement value in the judged unit — CPU seconds or wall-clock seconds minus conversion and imports; for steps, the judged steps (for a simulated world, the run part; a measurement
    without counts is not judged by steps)."""
    match unit:
        case "cpu-seconds":
            return measurement.judged_cpu_seconds
        case "wall-seconds":
            return measurement.judged_wall_seconds
        case "tree-cpu-seconds":
            return measurement.judged_tree_cpu_seconds
        case "steps":
            return float(measurement.judged_steps)


@dataclass(frozen=True)
class OverBudget:
    """Exceeded the budget (not in the registry). budget = the judged budget (in the unit of unit)."""

    measurement: Measurement
    budget: float
    unit: Unit = "cpu-seconds"


@dataclass(frozen=True)
class RegisteredOverBudget:
    """Exceeded the budget but is in the registry (not turned red)."""

    measurement: Measurement
    unit: Unit = "cpu-seconds"


@dataclass(frozen=True)
class RegisteredWithinBudget:
    """In the registry but finished within the budget (a removable registration). budget = the judged budget (in the unit of unit)."""

    measurement: Measurement
    budget: float
    unit: Unit = "cpu-seconds"

    @property
    def stale(self) -> bool:
        """Whether to turn red as a stale registration — only when finished at or below budget × STALE_RATIO."""
        return judged_value(self.measurement, self.unit) <= self.budget * STALE_RATIO


@dataclass(frozen=True)
class StepDeclaration:
    """Needed steps declared for one test, and the reason (one line) — only a test that stays outside the default budget puts it next to the test definition."""

    steps: int
    reason: str


def declare_step_budget(test: Callable[..., object], steps: int, reason: str) -> None:
    """Declare the steps one test needs — the budget plugin judges only that test by the declared value instead of the default budget.

    The default budget is unchanged; place it only on a test whose assertions depend on the scenario scale and so cannot be shrunk (decision of the Mac coordinator, 2026-10-03 21:5x ·
    in place of a registry in a separate file). Exceeding the declared value is red; finishing at or below
    STALE_RATIO (half) of the declared value is red as a stale declaration (same rule as a stale registry line). In a Hy test, right after deftest write
    ``(declare-step-budget test-name steps "reason")``. An unreadable value stops at import time."""
    if isinstance(steps, bool) or not isinstance(steps, int) or steps <= 0:
        raise ValueError(f"needed steps must be a positive integer: {steps!r}")
    if not isinstance(reason, str) or not reason.strip() or "\n" in reason.strip():
        raise ValueError(f"reason must be a non-empty single line: {reason!r}")
    setattr(test, DECLARED_STEPS_ATTRIBUTE, StepDeclaration(steps, reason.strip()))


def declared_steps_of(test: object) -> StepDeclaration | None:
    """The declaration attached to a test function (None if none)."""
    declaration = getattr(test, DECLARED_STEPS_ATTRIBUTE, None)
    return declaration if isinstance(declaration, StepDeclaration) else None


@dataclass(frozen=True)
class TreeCpuDeclaration:
    """One test's declaration "judge the marker-seconds budget by process-tree CPU seconds" and its reason (one line — why the weight is in child-process CPU and it does not wait on the outside)."""

    reason: str


def declare_process_tree_cpu(test: Callable[..., object], reason: str) -> None:
    """Declare that one test hitting a marker-seconds budget is judged by process-tree CPU seconds (own plus descendants, user+sys), not wall-clock
    (the "per-test declaration declare_process_tree_cpu" in the header note). The budget value stays the marker's seconds.

    Place it only on a test whose weight is in the CPU of child processes (and its own threads) and that does not wait on the outside (socket, HTTP, listener) — waiting does not show in CPU seconds.
    Wall-clock remains as a safety valve (marker seconds × TREE_CPU_WALL_BACKSTOP_FACTOR). Placing it on a test that hits no marker budget does not change the judgment.
    In a Hy test, right after deftest write ``(declare-process-tree-cpu test-name "reason")``. An unreadable value stops at import time."""
    if not isinstance(reason, str) or not reason.strip() or "\n" in reason.strip():
        raise ValueError(f"reason must be a non-empty single line: {reason!r}")
    setattr(test, DECLARED_TREE_CPU_ATTRIBUTE, TreeCpuDeclaration(reason.strip()))


def declared_tree_cpu_of(test: object) -> TreeCpuDeclaration | None:
    """The tree-CPU declaration attached to a test function (None if none)."""
    declaration = getattr(test, DECLARED_TREE_CPU_ATTRIBUTE, None)
    return declaration if isinstance(declaration, TreeCpuDeclaration) else None


@dataclass(frozen=True)
class DeclaredOverBudget:
    """Exceeded the declared needed steps (red)."""

    measurement: Measurement
    declaration: StepDeclaration


@dataclass(frozen=True)
class StaleDeclaration:
    """Finished at or below STALE_RATIO of the declared needed steps — a stale declaration (red)."""

    measurement: Measurement
    declaration: StepDeclaration


Verdict = (
    WithinBudget
    | OverBudget
    | RegisteredOverBudget
    | RegisteredWithinBudget
    | DeclaredOverBudget
    | StaleDeclaration
)


class RegistryError(Exception):
    """The shape of a registry file does not follow the rules."""


class BudgetWarning(pytest.PytestWarning):
    """Warning for a test that exceeded the budget in report-only mode."""


def registry_file_name(key: str) -> str:
    """Build the file name of a registry key (first 12 chars of the key's sha256 + .txt) — so that concurrently running workers do not collide by rewriting the same line."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12] + ".txt"


def load_registry(directory: Path) -> dict[str, str]:
    """Read a registry dir as key -> reason — so listed tests are not turned red. Empty if the dir is absent. A file whose name does not match the key's hash, or whose reason is empty, stops."""
    if not directory.is_dir():
        return {}
    table: dict[str, str] = {}
    for path in sorted(directory.glob("*.txt")):
        lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
        key = lines[0].strip()
        reason = "\n".join(lines[1:]).strip()
        if path.name != registry_file_name(key):
            raise RegistryError(
                f"the name of {path} does not match the key hash (the name for key {key!r} is {registry_file_name(key)})"
            )
        if not reason:
            raise RegistryError(f"{path} has no reason (write the reason from line 2)")
        table[key] = reason
    return table


def judge(
    measurement: Measurement, budget: float, registry: Mapping[str, str], unit: Unit = "cpu-seconds"
) -> Verdict:
    """Check one measured span against the budget and registry in seconds (unit = CPU seconds or wall-clock seconds) minus conversion and first dependency imports (so the weight of a missing cache
    is not counted as the test's weight)."""
    if judged_value(measurement, unit) <= budget:
        if measurement.key in registry:
            return RegisteredWithinBudget(measurement, budget, unit)
        return WithinBudget(measurement)
    if measurement.key in registry:
        return RegisteredOverBudget(measurement, unit)
    return OverBudget(measurement, budget, unit)


def judge_steps(measurement: Measurement, budget_steps: int, registry: Mapping[str, str]) -> Verdict:
    """Check one measured span against the step budget and step registry — independent of machine load, so the same test gets the same verdict every time (#2670)."""
    steps = judged_value(measurement, "steps")
    if steps <= budget_steps:
        if measurement.key in registry:
            return RegisteredWithinBudget(measurement, float(budget_steps), "steps")
        return WithinBudget(measurement)
    if measurement.key in registry:
        return RegisteredOverBudget(measurement, "steps")
    return OverBudget(measurement, float(budget_steps), "steps")


def judge_declared(measurement: Measurement, declaration: StepDeclaration) -> Verdict:
    """Judge one test with a declaration by the declared value, not the default budget — exceeding is red; at or below the declaration's STALE_RATIO is a stale declaration and red."""
    steps = judged_value(measurement, "steps")
    if steps > declaration.steps:
        return DeclaredOverBudget(measurement, declaration)
    if steps <= declaration.steps * STALE_RATIO:
        return StaleDeclaration(measurement, declaration)
    return WithinBudget(measurement)


@dataclass(frozen=True)
class SettingError:
    """A setting value cannot be read (with a message) — stops at session start with UsageError."""

    message: str


def parse_positive_seconds(raw: str) -> float | SettingError:
    """Read a seconds string as a positive number. A non-number or non-positive value is an error, not silently defaulted."""
    try:
        value = float(raw)
    except ValueError:
        return SettingError(f"a positive number of seconds: {raw!r}")
    if value <= 0:
        return SettingError(f"a positive number of seconds: {raw!r}")
    return value


def parse_marker_budgets(lines: Sequence[str]) -> Mapping[str, float] | SettingError:
    """Read per-marker run-budget lines (``marker=seconds``) as marker -> seconds. A line without =, an empty marker, unreadable seconds, or two lines for the same marker is an error."""
    table: dict[str, float] = {}
    for line in lines:
        marker, sep, raw = line.partition("=")
        marker = marker.strip()
        if not sep or not marker:
            return SettingError(f"a line of {MARKER_CALL_BUDGET_INI} must be in the form marker=seconds: {line!r}")
        if marker in table:
            return SettingError(f"{MARKER_CALL_BUDGET_INI} has two lines for marker {marker}")
        match parse_positive_seconds(raw.strip()):
            case SettingError(message=message):
                return SettingError(f"{MARKER_CALL_BUDGET_INI}: for {marker}, {message}")
            case float() as seconds:
                table[marker] = seconds
    return table


def call_budget_for(markers: Iterable[str], by_marker: Mapping[str, float]) -> float | None:
    """Choose the run-seconds budget for one test — the longest of the matching markers' seconds; None if no marker matches (not judged by seconds)."""
    matched = [by_marker[name] for name in markers if name in by_marker]
    return max(matched) if matched else None


def judge_call(
    budgets: Budgets,
    markers: Sequence[str],
    measurement: Measurement,
    declaration: StepDeclaration | None,
    tree_cpu: TreeCpuDeclaration | None = None,
) -> Verdict | None:
    """Judge the run (call) of one test that passed (None = not measured). A test hitting a per-marker seconds budget (a test with real I/O — real_world etc.)
    is judged by wall-clock seconds since its weight shows no steps (the weight is in child processes and waiting and does not show in process CPU seconds).
    Everything else is judged by the step budget (a test with a declaration by the declared value). A test hitting neither the step budget nor a marker budget
    is not judged. A test that hits a marker budget and declared tree CPU seconds (tree_cpu) is judged by the same seconds as tree CPU seconds only when the tree could be measured, and
    if wall-clock exceeds the safety valve (seconds × TREE_CPU_WALL_BACKSTOP_FACTOR) it is red regardless of CPU seconds."""
    marker_seconds = call_budget_for(markers, budgets.call_seconds_by_marker)
    if marker_seconds is not None:
        if tree_cpu is not None and isinstance(measurement.tree, TreeCpu):
            backstop = marker_seconds * TREE_CPU_WALL_BACKSTOP_FACTOR
            if measurement.judged_wall_seconds > backstop and measurement.key not in budgets.registry:
                return OverBudget(measurement, backstop, "wall-seconds")
            return judge(measurement, marker_seconds, budgets.registry, "tree-cpu-seconds")
        return judge(measurement, marker_seconds, budgets.registry, "wall-seconds")
    match budgets.call_steps:
        case None:
            return None
        case int() as steps:
            if declaration is None:
                return judge_steps(measurement, steps, budgets.registry)
            return judge_declared(measurement, declaration)


def load_registries(root: Path, directories: Sequence[str]) -> dict[str, str]:
    """Read the registry dirs in order and merge into key -> reason over all dirs (if the same key is in two dirs, the earlier dir's reason)."""
    table: dict[str, str] = {}
    for directory in directories:
        for key, reason in load_registry(root / directory).items():
            table.setdefault(key, reason)
    return table


def _seconds_text(measurement: Measurement) -> str:
    """Wording of a measurement's seconds (wall-clock seconds and CPU seconds minus conversion, imports, and GC; the subtracted spans; steps). Per-marker runs are judged by wall-clock seconds and
    collect by CPU seconds, so the sentence does not put the judged side's seconds first but lists both in the same form."""
    compile_part = (
        f"{measurement.compile.count} uncached conversions, wall-clock {measurement.compile.wall_seconds:.3f} s, "
        f"CPU {measurement.compile.cpu_seconds:.3f} s subtracted"
        if measurement.compile.count
        else None
    )
    imports_part = (
        f"{measurement.imports.count} first imports of dependency modules, wall-clock {measurement.imports.wall_seconds:.3f} s, "
        f"CPU {measurement.imports.cpu_seconds:.3f} s subtracted"
        if measurement.imports.count
        else None
    )
    gc_part = (
        f"{measurement.gc.count} GC runs, wall-clock {measurement.gc.wall_seconds:.3f} s, CPU {measurement.gc.cpu_seconds:.3f} s subtracted"
        if measurement.gc.count
        else None
    )
    world = measurement.world
    world_part = (
        f"(world run {world.running}, start {world.starting}, stop {world.stopping}, outside the world {world.outside}, "
        f"of {world.worlds} worlds {world.up} started up — the run is what is judged)"
        if world is not None
        else ""
    )
    work_part = (
        f"doeff-vm steps {measurement.work.steps}{world_part}, handler calls {measurement.work.handler_calls}"
        if measurement.work is not None
        else None
    )
    match measurement.tree:
        case TreeCpu() as tree:
            tree_part = (
                f"process-tree CPU {measurement.judged_tree_cpu_seconds:.3f} s (own {tree.own_seconds:.3f} s, "
                f"descendants {tree.processes} procs {tree.descendants_seconds:.3f} s)"
            )
        case TreeCpuUnmeasured(reason=reason):
            tree_part = f"process-tree CPU seconds could not be measured ({reason}) — judged by wall-clock"
        case None:
            tree_part = None
    parts = tuple(part for part in (tree_part, compile_part, imports_part, gc_part, work_part) if part is not None) or (
        "no span subtracted",
    )
    return (
        f"wall-clock {measurement.judged_wall_seconds:.3f} s, CPU {measurement.judged_cpu_seconds:.3f} s ("
        + "・".join(parts)
        + ")"
    )


def _limit_text(budget: float, unit: Unit) -> str:
    """Wording of a budget (with its unit) — so CPU seconds, wall-clock seconds, and steps are not confused in a sentence."""
    match unit:
        case "cpu-seconds":
            return f"CPU {budget:.3f} s"
        case "wall-seconds":
            return f"wall-clock {budget:.3f} s"
        case "tree-cpu-seconds":
            return f"process-tree CPU {budget:.3f} s"
        case "steps":
            return f"steps {int(budget)}"


def over_budget_message(verdict: OverBudget, registry_dirs: Sequence[str]) -> str:
    """Message for a test that exceeded the budget — to give the reader how many seconds (steps) it took, the budget, and how to fix it (the place to add is the first dir of that unit's registry)."""
    m = verdict.measurement
    what = "run (call)" if m.phase == "call" else "load (import)"
    where = registry_dirs[0] if registry_dirs else f"the dir named by {REGISTRY_INI}"
    return (
        f"doeff test time limit exceeded: {m.key} {what} took {_seconds_text(m)}; limit {_limit_text(verdict.budget, verdict.unit)}. "
        "Make the test faster (shrink the mock world; wake waits with writes), or if it cannot be fixed, "
        f"add a file {registry_file_name(m.key)} for key {m.key!r} to {where} with a reason (the registry may only shrink)."
    )


def declared_over_message(verdict: DeclaredOverBudget) -> str:
    """A message for a test that exceeded its declared required steps (how many steps, the declared value, the declared reason)."""
    m = verdict.measurement
    return (
        f"doeff test exceeded its declared steps: {m.key} run (call) took {_seconds_text(m)}; declared steps {verdict.declaration.steps}"
        f" (reason: {verdict.declaration.reason}). Make the test faster, or check why the scale the assertion depends on grew before changing the declared value."
    )


def stale_declaration_message(verdict: StaleDeclaration) -> str:
    """A message for a stale declaration that turned red (which test, how many steps, how much of the declaration)."""
    m = verdict.measurement
    return (
        f"{m.key} declares steps {verdict.declaration.steps}, but its run (call) is at most {STALE_RATIO:g} times that"
        f" ({_seconds_text(m)}); this is a stale declaration, so remove it or lower the value (a declaration is not needed within the default limit)"
    )


def stale_registration_message(verdict: RegisteredWithinBudget, registry_dirs: Sequence[str]) -> str:
    """A message for a stale registry entry that turned red (which test, how many seconds [steps], what fraction of the limit, which file to delete)."""
    m = verdict.measurement
    return (
        f"{m.key} is in the registry, but its {m.phase} is at most {STALE_RATIO:g} times the limit of {_limit_text(verdict.budget, verdict.unit)}"
        f" ({_seconds_text(m)}); this is a stale registry entry, so delete the registry file "
        f"{registry_file_name(m.key)} (the registry may only shrink)"
    )


class CompileCounter:
    """Counts, and CPU seconds of, the intervals subtracted from a measurement (conversion from source to code, and the first import of another module), so that a test's weight
    excludes the uncached weight and the one-time import weight of a shared dependency.

    ``SourceFileLoader.source_to_code`` is called only on a bytecode cache miss, and Hy's import also goes through the same entry point that Hy
    replaces (Hy's replacement calls the original again, so it is counted whichever of Hy and this counter replaces it first).
    Conversion triggers imports and conversions of other modules (Hy's ``require``), so seconds are added only to the outermost interval, in the column for that interval's kind
    (conversion inside a dependency's import goes into the dependency-import column exactly once, so nothing is subtracted twice).

    The first import of another module is an ``importlib._bootstrap._find_and_load`` interval (both import statements and ``importlib.import_module``
    pass through here when the name is not in sys.modules). The one-time weight of a shared dependency lands on the first test file imported in that process,
    so without subtracting it, the same file's verdict changes with ordering, with running only one file, or with whether the test index is cold
    (only the first collection with a cold index went red). The test file itself is loaded by doeff-adr via
    ``module_from_spec`` and ``exec_module``, so it is not in this interval, and the weight of the file's own body stays in the measurement.

    If doeff-effect-analyzer is installed, its Hy expansion (which runs only on an expanded-syntax-tree cache miss, so likewise
    uncached weight) is also added to the conversion count (via the ``observe_expansions`` entry point; the analyzer does not know who counts).

    GC (from the start to the stop of ``gc.callbacks``) is also counted as a subtracted interval. GC cost depends on the number of live objects in the process
    (a free-threaded Python GC does not split generations and walks the whole heap each time; one run at 3 million objects takes about 0.2 seconds), and when it happens is decided by
    the cycle of allocation counts, so if it were in the measurement, the verdict would swap depending on which test in the session it hits (in the agora-controllers daily run,
    a file that takes 0.03 seconds alone took 1.0 to 1.4 seconds, and the failing file changed from run to run). GC that happened inside a conversion or dependency first-import
    interval is already in that interval's seconds, so it is not counted in the GC column (nothing is subtracted twice).
    """

    def __init__(self) -> None:
        self._mut_count = 0
        self._mut_cpu_seconds = 0.0
        self._mut_wall_seconds = 0.0
        self._mut_import_count = 0
        self._mut_import_cpu_seconds = 0.0
        self._mut_import_wall_seconds = 0.0
        self._mut_gc_count = 0
        self._mut_gc_cpu_seconds = 0.0
        self._mut_gc_wall_seconds = 0.0
        # Start time of a running GC pass (CPU seconds, wall-clock seconds): set on the start signal, added to the column and removed on the stop signal.
        self._mut_gc_started: tuple[float, float] | None = None
        self._mut_depth = 0
        self._mut_outer: SetAside | None = None
        self._mut_original: Callable[..., object] | None = None
        self._mut_original_find_and_load: Callable[..., object] | None = None
        self._mut_stop_observing: Callable[[], None] | None = None

    def tally(self) -> CompileTally:
        """The conversion counts so far (used as the difference before and after an interval)."""
        return CompileTally(self._mut_count, self._mut_cpu_seconds, self._mut_wall_seconds)

    def import_tally(self) -> ImportTally:
        """The dependency first-import counts so far (used as the difference before and after an interval)."""
        return ImportTally(self._mut_import_count, self._mut_import_cpu_seconds, self._mut_import_wall_seconds)

    def gc_tally(self) -> GcTally:
        """The GC counts so far (used as the difference before and after an interval)."""
        return GcTally(self._mut_gc_count, self._mut_gc_cpu_seconds, self._mut_gc_wall_seconds)

    def _on_gc(self, phase: str, info: Mapping[str, object]) -> None:
        """The ``gc.callbacks`` entry point: adds the CPU seconds and wall-clock seconds of one GC pass that happened outside conversion and dependency first-import intervals to the GC column."""
        if self._mut_depth:
            return
        match phase:
            case "start":
                self._mut_gc_started = (time.process_time(), time.perf_counter())
            case "stop" if self._mut_gc_started is not None:
                cpu_started, wall_started = self._mut_gc_started
                self._mut_gc_count += 1
                self._mut_gc_cpu_seconds += time.process_time() - cpu_started
                self._mut_gc_wall_seconds += time.perf_counter() - wall_started
                self._mut_gc_started = None
            case _:
                pass

    @contextlib.contextmanager
    def _set_aside(self, kind: SetAside) -> Generator[None]:
        """One subtracted interval: if outermost, adds its CPU seconds and wall-clock seconds to the column for its kind (collection is judged in CPU seconds and
        per-marker runs in wall-clock seconds, so it can subtract in both units)."""
        if kind == "compile":
            self._mut_count += 1
        else:
            self._mut_import_count += 1
        if self._mut_depth == 0:
            self._mut_outer = kind
        self._mut_depth += 1
        cpu_started = time.process_time()
        wall_started = time.perf_counter()
        try:
            yield
        finally:
            self._mut_depth -= 1
            if self._mut_depth == 0:
                cpu_elapsed = time.process_time() - cpu_started
                wall_elapsed = time.perf_counter() - wall_started
                if self._mut_outer == "compile":
                    self._mut_cpu_seconds += cpu_elapsed
                    self._mut_wall_seconds += wall_elapsed
                else:
                    self._mut_import_cpu_seconds += cpu_elapsed
                    self._mut_import_wall_seconds += wall_elapsed
                self._mut_outer = None

    def converting(self) -> contextlib.AbstractContextManager[None]:
        """One uncached conversion interval."""
        return self._set_aside("compile")

    def importing(self) -> contextlib.AbstractContextManager[None]:
        """One first-import interval of another module."""
        return self._set_aside("import")

    def install(self) -> None:
        """Install the counting hooks (only for the session; uninstall restores them)."""
        original = importlib.machinery.SourceFileLoader.source_to_code
        self._mut_original = original
        counter = self

        def counting_source_to_code(loader: object, *args: object, **kwargs: object) -> object:
            """Call the original conversion and count it as an uncached conversion interval."""
            with counter.converting():
                return original(loader, *args, **kwargs)

        importlib.machinery.SourceFileLoader.source_to_code = counting_source_to_code  # type: ignore[method-assign]  # replacement for counting (restored by uninstall)
        bootstrap = sys.modules[_BOOTSTRAP_MODULE]
        original_find_and_load = getattr(bootstrap, _FIND_AND_LOAD)
        self._mut_original_find_and_load = original_find_and_load

        def counting_find_and_load(name: str, import_: object) -> object:
            """Call the original import and, if the name is not in sys.modules, count it as a dependency first-import interval."""
            if name in sys.modules:
                return original_find_and_load(name, import_)
            with counter.importing():
                return original_find_and_load(name, import_)

        # Replacement for counting (restored by uninstall); the import machinery looks up this name from the module on every call, so it also affects import statements.
        setattr(bootstrap, _FIND_AND_LOAD, counting_find_and_load)
        self._mut_stop_observing = observe_analyzer_expansions(self.converting)
        gc.callbacks.append(self._on_gc)

    def uninstall(self) -> None:
        """Restore the hooks from before install."""
        if self._on_gc in gc.callbacks:
            gc.callbacks.remove(self._on_gc)
        if self._mut_original is not None:
            importlib.machinery.SourceFileLoader.source_to_code = self._mut_original  # type: ignore[method-assign]  # restore the state before install
            self._mut_original = None
        if self._mut_original_find_and_load is not None:
            setattr(sys.modules[_BOOTSTRAP_MODULE], _FIND_AND_LOAD, self._mut_original_find_and_load)
            self._mut_original_find_and_load = None
        if self._mut_stop_observing is not None:
            self._mut_stop_observing()
            self._mut_stop_observing = None


def observe_analyzer_expansions(
    converting: Callable[[], contextlib.AbstractContextManager[None]],
) -> Callable[[], None] | None:
    """So that doeff-effect-analyzer's Hy expansion also counts (does nothing, returning None, in an environment without the analyzer).

    observe_expansions is a name internal to the analyzer (ADR-DOE-API-001 R24): a process-wide entry point that is not something to request inside a program, so it is not an effect
    and not exposed at the entry. Only this plugin inside doeff reads it."""
    try:
        from doeff_effect_analyzer._program_effects import observe_expansions
    except ImportError:
        return None
    return observe_expansions(converting)


@dataclass(frozen=True)
class CallMeasured:
    """The call phase's CPU seconds and wall-clock seconds, plus conversion and dependency first-import (kept on the item until judged in makereport)."""

    cpu_seconds: float
    wall_seconds: float
    compile: CompileTally
    imports: ImportTally
    work: VmWork | None = None
    gc: GcTally = NO_GC
    world: WorldSteps | None = None
    python_calls: int | None = None
    tree: TreeCpu | TreeCpuUnmeasured | None = None


_BUDGETS_KEY = pytest.StashKey[Budgets]()
_COUNTER_KEY = pytest.StashKey[CompileCounter]()
_VERDICTS_KEY = pytest.StashKey[list[Verdict]]()
_CALL_MEASURED_KEY = pytest.StashKey[CallMeasured]()
# Counts of a run with the --doeff-python-calls marker (no entry is kept for a run without the marker).
_PYTHON_CALLS_KEY = pytest.StashKey[CallCounting]()


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the setting names and command-line flag with pytest."""
    parser.addoption(
        PYTHON_CALLS_OPTION,
        dest=PYTHON_CALLS_DEST,
        action="store_true",
        default=False,
        help="Count Python calls (sys.monitoring CALL events) during the call phase of tests that declare steps, and print one line at the end (once a day only)",
    )
    parser.addini(
        MARKER_CALL_BUDGET_INI,
        "Per-marker wall-clock-seconds limit for one Hy test run (1 line = marker=seconds; if several markers match, the longest seconds; a matching test is judged in seconds even if a step limit exists)",
        type="linelist",
        default=[],
    )
    parser.addini(
        COLLECT_BUDGET_INI,
        "CPU-seconds limit for importing one Hy test file's module (empty = not measured)",
        default="",
    )
    parser.addini(
        MODE_INI,
        "What to do when a limit is exceeded: report (warning and list only; default) or fail (turn red)",
        default="report",
    )
    parser.addini(
        REGISTRY_INI,
        "Registry dirs of existing tests allowed to exceed the limit (relative to rootdir; 1 line = 1 dir)",
        type="linelist",
        default=[],
    )
    parser.addini(
        CALL_STEPS_INI,
        "doeff-vm step limit for one Hy test run (positive integer; default = the default limit; empty = run is limited only by per-marker seconds)",
        default="",
    )
    parser.addini(
        STEPS_COUNTED_INI,
        "Span counted when judging by steps: call (the whole call; default) or world-running (for a test that ran a mock world, only the steps of the world run)",
        default="call",
    )
    parser.addini(
        RUNAWAY_MULTIPLIER_INI,
        "Multiplier of the step limit at which a test is killed while running (step limit x this value; positive integer; empty = default 20)",
        default="",
    )


def _seconds(config: pytest.Config, name: str) -> float | None:
    """Read a limit in seconds (empty = not measured). A non-number or non-positive value stops with an error instead of silently falling back to the default."""
    raw = str(config.getini(name)).strip()
    if not raw:
        return None
    match parse_positive_seconds(raw):
        case SettingError(message=message):
            raise pytest.UsageError(f"{name} must be {message}")
        case float() as value:
            return value


def _marker_seconds(config: pytest.Config) -> Mapping[str, float]:
    """Read the per-marker run limits. An unreadable line stops with an error."""
    match parse_marker_budgets([str(line) for line in config.getini(MARKER_CALL_BUDGET_INI)]):
        case SettingError(message=message):
            raise pytest.UsageError(message)
        case table:
            return table


def parse_positive_steps(raw: str) -> int | SettingError:
    """Read a step-count string as a positive integer (`default` is the default limit DEFAULT_CALL_BUDGET_STEPS). A non-number or non-positive value is an error instead of silently falling back to the default."""
    if raw == "default":
        return DEFAULT_CALL_BUDGET_STEPS
    try:
        value = int(raw)
    except ValueError:
        return SettingError(f"a positive integer step count: {raw!r}")
    if value <= 0:
        return SettingError(f"a positive integer step count: {raw!r}")
    return value


def _steps(config: pytest.Config) -> int | None:
    """Read the step limit (empty = not judged by steps). An unreadable value stops with an error."""
    raw = str(config.getini(CALL_STEPS_INI)).strip()
    if not raw:
        return None
    match parse_positive_steps(raw):
        case SettingError(message=message):
            raise pytest.UsageError(f"{CALL_STEPS_INI} must be {message}")
        case int() as value:
            return value


def _runaway_multiplier(config: pytest.Config) -> int:
    """Read the multiplier of the in-flight step limit (empty = default). A value that is not a positive integer stops with an error instead of silently falling back to the default."""
    raw = str(config.getini(RUNAWAY_MULTIPLIER_INI)).strip()
    if not raw:
        return DEFAULT_RUNAWAY_STEPS_MULTIPLIER
    try:
        value = int(raw)
    except ValueError:
        raise pytest.UsageError(f"{RUNAWAY_MULTIPLIER_INI} must be a positive integer: {raw!r}") from None
    if value <= 0:
        raise pytest.UsageError(f"{RUNAWAY_MULTIPLIER_INI} must be a positive integer: {raw!r}")
    return value


def _steps_counted(config: pytest.Config) -> StepsCounted:
    """Read the span counted in steps. A value outside the vocabulary stops with an error instead of silently falling back to the default."""
    raw = str(config.getini(STEPS_COUNTED_INI)).strip()
    if raw == "call":
        return "call"
    if raw == "world-running":
        return "world-running"
    raise pytest.UsageError(f"{STEPS_COUNTED_INI} must be call or world-running: {raw!r}")


def _phase_log(config: pytest.Config, call_steps: int | None) -> PhaseLogSource:
    """Look for the world-boundary marker entry point only in repos that count by run steps (a repo that counts the whole call does not read markers)."""
    if call_steps is None:
        return NoWorldPhaseLog("no step limit is configured")
    if _steps_counted(config) == "call":
        return NoWorldPhaseLog(f"{STEPS_COUNTED_INI} is call")
    return read_phase_log()


def _mode(config: pytest.Config) -> Mode:
    """Read what to do when a limit is exceeded. A value outside the vocabulary stops with an error."""
    raw = str(config.getini(MODE_INI)).strip()
    if raw == "report":
        return "report"
    if raw == "fail":
        return "fail"
    raise pytest.UsageError(f"{MODE_INI} must be report or fail: {raw!r}")


def pytest_configure(config: pytest.Config) -> None:
    """If at least one limit is configured, read the registry and install the conversion counter. A build whose doeff-vm lacks the count entry point while a step limit exists
    stops with an error naming it, instead of falling back to seconds."""
    call_seconds_by_marker = _marker_seconds(config)
    collect_seconds = _seconds(config, COLLECT_BUDGET_INI)
    call_steps = _steps(config)
    counts_python_calls = bool(config.getoption(PYTHON_CALLS_DEST))
    if not call_seconds_by_marker and collect_seconds is None and call_steps is None:
        if counts_python_calls:
            config.stash[_PYTHON_CALLS_KEY] = NotCounted("no doeff test limit is configured; steps are not measured")
        return
    work_source = read_work_source()
    if call_steps is not None and isinstance(work_source, NoWorkReader):
        raise pytest.UsageError(
            f"{CALL_STEPS_INI} is set but steps cannot be measured ({work_source.reason}); stopping because the run cannot be judged by steps"
            " (it does not fall back to CPU seconds). Run on a doeff-vm build that has the count entry point, or remove the step-limit setting"
        )
    mode = _mode(config)
    registry_dirs = tuple(str(line) for line in config.getini(REGISTRY_INI))
    try:
        registry = load_registries(Path(config.rootpath), registry_dirs)
    except RegistryError as exc:
        raise pytest.UsageError(str(exc)) from None
    config.stash[_BUDGETS_KEY] = Budgets(
        call_seconds_by_marker,
        collect_seconds,
        mode,
        registry,
        registry_dirs,
        read_vm_build(),
        call_steps=call_steps,
        work_source=work_source,
        step_guard=read_step_guard() if call_steps is not None else NoSchedulerStepGuard("no step limit is configured"),
        runaway_multiplier=_runaway_multiplier(config),
        phase_log=_phase_log(config, call_steps),
    )
    counter = CompileCounter()
    counter.install()
    config.stash[_COUNTER_KEY] = counter
    config.stash[_VERDICTS_KEY] = []
    if counts_python_calls:
        config.stash[_PYTHON_CALLS_KEY] = (
            CallCounter.acquire()
            if isinstance(work_source, WorkReader)
            else NotCounted(f"steps cannot be measured ({work_source.reason})")
        )


def pytest_unconfigure(config: pytest.Config) -> None:
    """Remove the installed conversion counter and return the frame for Python call counting."""
    counter = config.stash.get(_COUNTER_KEY, None)
    if counter is not None:
        counter.uninstall()
    python_calls = config.stash.get(_PYTHON_CALLS_KEY, None)
    if isinstance(python_calls, CallCounter):
        python_calls.release()


def _relative_key(path: Path, root: Path) -> str:
    """The collection key (path from rootdir)."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _record(config: pytest.Config, verdict: Verdict) -> bool:
    """Add the verdict to the session list and return whether it should turn red (in report-only mode, only a warning is emitted and it does not turn red)."""
    config.stash[_VERDICTS_KEY].append(verdict)
    budgets = config.stash[_BUDGETS_KEY]
    match verdict:
        case OverBudget():
            if budgets.fails_for(verdict.unit):
                return True
            warnings.warn(
                BudgetWarning(over_budget_message(verdict, budgets.registry_dirs)), stacklevel=1
            )
            return False
        case RegisteredWithinBudget():
            return verdict.stale and budgets.fails_for(verdict.unit)
        case DeclaredOverBudget():
            if budgets.fails_for("steps"):
                return True
            warnings.warn(BudgetWarning(declared_over_message(verdict)), stacklevel=1)
            return False
        case StaleDeclaration():
            return budgets.fails_for("steps")
        case WithinBudget() | RegisteredOverBudget():
            return False


def _failure_message(verdict: Verdict, budgets: Budgets) -> str:
    """The message for a verdict that turned red (where to add or remove: the registry of the judged unit; for a declaration, next to the test's definition)."""
    match verdict:
        case OverBudget():
            return over_budget_message(verdict, budgets.registry_dirs)
        case RegisteredWithinBudget():
            return stale_registration_message(verdict, budgets.registry_dirs)
        case DeclaredOverBudget():
            return declared_over_message(verdict)
        case StaleDeclaration():
            return stale_declaration_message(verdict)
        case WithinBudget() | RegisteredOverBudget():
            raise AssertionError(f"asked for the message of a verdict that does not turn red: {verdict}")


@pytest.hookimpl(wrapper=True, optionalhook=True)
def pytest_doeff_import_hy_module(
    collector: pytest.Module,
) -> Generator[None, types.ModuleType, types.ModuleType]:
    """Measure and judge the module import of one Hy test file (a doeff-adr hook, both during collection and during setup)."""
    config = collector.config
    budgets = config.stash.get(_BUDGETS_KEY, None)
    if budgets is None or budgets.collect_seconds is None or collector.path.suffix != ".hy":
        return (yield)
    counter = config.stash[_COUNTER_KEY]
    compile_before = counter.tally()
    imports_before = counter.import_tally()
    gc_before = counter.gc_tally()
    cpu_started = time.process_time()
    wall_started = time.perf_counter()
    module = yield
    measurement = Measurement(
        key=_relative_key(collector.path, Path(config.rootpath)),
        phase="collect",
        cpu_seconds=time.process_time() - cpu_started,
        wall_seconds=time.perf_counter() - wall_started,
        compile=counter.tally().since(compile_before),
        imports=counter.import_tally().since(imports_before),
        gc=counter.gc_tally().since(gc_before),
    )
    verdict = judge(measurement, budgets.collect_seconds, budgets.registry)
    if _record(config, verdict):
        pytest.fail(_failure_message(verdict, budgets), pytrace=False)
    return module


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, None, None]:
    """Measure the call phase's CPU seconds and conversion and put them on the item (judged in makereport). For a Hy test judged by steps, during the call only,
    put a step limit (step limit x multiplier) on the scheduler so that a spin that never finishes is killed while running (#3143). In a run with the
    --doeff-python-calls marker, Python calls are counted only during the call of tests that declare steps (#2854)."""
    counter = item.config.stash.get(_COUNTER_KEY, None)
    if counter is None:
        return (yield)
    budgets = item.config.stash[_BUDGETS_KEY]
    source = budgets.work_source
    guard = budgets.step_guard
    declaration = declared_steps_of(getattr(item, "obj", None))
    runaway_steps = (
        budgets.runaway_steps_for(
            [marker.name for marker in item.iter_markers()],
            declaration.steps if declaration is not None else None,
        )
        if item.path.suffix == ".hy" and isinstance(guard, SchedulerStepGuard)
        else None
    )
    phase_log = budgets.phase_log if isinstance(source, WorkReader) else NoWorldPhaseLog("steps are not measured")
    work_before = source.tally() if isinstance(source, WorkReader) else None
    compile_before = counter.tally()
    imports_before = counter.import_tally()
    gc_before = counter.gc_tally()
    cpu_started = time.process_time()
    wall_started = time.perf_counter()
    if isinstance(phase_log, WorldPhaseLog):
        phase_log.open()
    if isinstance(guard, SchedulerStepGuard) and runaway_steps is not None:
        guard.arm(runaway_steps)
    python_calls = item.config.stash.get(_PYTHON_CALLS_KEY, None)
    calls_counter = python_calls if isinstance(python_calls, CallCounter) and declaration is not None else None
    if calls_counter is not None:
        calls_counter.start()
    # The tree's CPU seconds are read only during the call of a Hy test that matches a marker's seconds limit and declares tree CPU seconds (other tests do not wake the reader thread).
    tree_sampler = (
        TreeCpuSampler()
        if item.path.suffix == ".hy"
        and declared_tree_cpu_of(getattr(item, "obj", None)) is not None
        and call_budget_for([marker.name for marker in item.iter_markers()], budgets.call_seconds_by_marker) is not None
        else None
    )
    tree_unmeasured = tree_sampler.start() if tree_sampler is not None else None
    try:
        return (yield)
    finally:
        tree = (
            tree_unmeasured
            if tree_unmeasured is not None
            else (tree_sampler.stop() if tree_sampler is not None else None)
        )
        calls = calls_counter.stop() if calls_counter is not None else None
        if isinstance(guard, SchedulerStepGuard) and runaway_steps is not None:
            guard.disarm()
        marks = (
            tuple(WorldMark(mark.phase, mark.steps) for mark in phase_log.close())
            if isinstance(phase_log, WorldPhaseLog)
            else ()
        )
        cpu_seconds = time.process_time() - cpu_started
        wall_seconds = time.perf_counter() - wall_started
        work_after = source.tally() if isinstance(source, WorkReader) else None
        work = work_after.since(work_before) if work_after is not None and work_before is not None else None
        item.stash[_CALL_MEASURED_KEY] = CallMeasured(
            cpu_seconds=cpu_seconds,
            wall_seconds=wall_seconds,
            compile=counter.tally().since(compile_before),
            imports=counter.import_tally().since(imports_before),
            work=work,
            gc=counter.gc_tally().since(gc_before),
            world=(
                world_steps(marks, work_before.steps, work_after.steps)
                if work_after is not None and work_before is not None
                else None
            ),
            python_calls=calls,
            tree=tree,
        )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Judge the call phase of one passed Hy test by the per-marker seconds limit or the step limit (a failed test stays failed as it is)."""
    report = yield
    config = item.config
    budgets = config.stash.get(_BUDGETS_KEY, None)
    measured = item.stash.get(_CALL_MEASURED_KEY, None)
    if (
        budgets is None
        or measured is None
        or call.when != "call"
        or report.outcome != "passed"
        or item.path.suffix != ".hy"
    ):
        return report
    markers = [marker.name for marker in item.iter_markers()]
    measurement = Measurement(
        key=item.nodeid,
        phase="call",
        cpu_seconds=measured.cpu_seconds,
        wall_seconds=measured.wall_seconds,
        compile=measured.compile,
        imports=measured.imports,
        work=measured.work,
        gc=measured.gc,
        world=measured.world,
        python_calls=measured.python_calls,
        tree=measured.tree,
    )
    test = getattr(item, "obj", None)
    verdict = judge_call(budgets, markers, measurement, declared_steps_of(test), declared_tree_cpu_of(test))
    if verdict is None:
        return report
    if _record(config, verdict):
        report.outcome = "failed"
        report.longrepr = _failure_message(verdict, budgets)
    return report


# -- Per-test CPU seconds / steps (us/step) ratio (section 2 of design #2670) --
# Report only (not included in the verdict, the exit code, or BudgetWarning). On a busy day all tests swing in the same direction, so outliers are found not by absolute value but by the ratio
# between tests in the same session (multiple of the session median). Basis of the values (node-h measurement of 2026-10-12, doeff branch):
# tests with 10,000 or more steps fall within 9.7 to 19.7 us/step, and the ratio within the same run
# is 0.85 to 1.14 between the 5th and 95th percentiles. Tests with fewer than 100 steps are numbers of fixed cost only (median 167, max 230,000 us/step), so they are not included in the ratio.
# Review the multiplier, floor, and minimum count after reading the first 5 daily lines (the 4 lines below are the only place to change the values).
# Step floor for inclusion in the ratio (tests below it only get a count).
RATIO_FLOOR_STEPS = 10_000
# A session with fewer tests than this above the floor prints no ratio (the median is meaningless).
RATIO_MIN_TESTS = 8
# Outlier = a test above this multiple of the median, or below 1/this.
RATIO_OUTLIER_FACTOR = 2.0
# Number of outliers named (per side; the rest are only counted).
RATIO_NAMED = 3
# Maximum characters of a named test's name (to fit on one line).
RATIO_NAME_CHARS = 56
# Head of the one ratio line: the daily report (controllers/daily_verify/core/pytest_report.hy in agora-controllers) finds and reads the line back by this head.
# If the spelling changes, fix read_step_cost_line and the agora-controllers reader (the round-trip test) in the same change.
RATIO_LINE_HEAD = "CPU seconds / steps (us/step): "
# Text added inside the parentheses when tests that counted Python calls are excluded from the ratio (one number).
RATIO_EXCLUDED_TEXT = "{} tests that counted Python calls are excluded because monitoring adds overhead"


@dataclass(frozen=True)
class StepCost:
    """One test's CPU seconds / steps (us/step) and its multiple of the session median. name = the short name used for naming (step_cost_name)."""

    name: str
    micros_per_step: float
    ratio: float


@dataclass(frozen=True)
class StepCostRatios:
    """The ratio for one session (when at least RATIO_MIN_TESTS tests are above the floor). above = tests above RATIO_OUTLIER_FACTOR times the median (in descending
    order of multiple), below = tests below 1/that (in ascending order of multiple); both hold all of them, and formatting names only the first RATIO_NAMED."""

    measured: int
    below_floor: int
    median: float
    highest: StepCost
    above: tuple[StepCost, ...]
    below: tuple[StepCost, ...]
    # Number of tests that counted Python calls (runs with the --doeff-python-calls marker): monitoring overhead makes CPU seconds 1.5 to 1.7 times larger, so they are not included in the ratio.
    excluded: int = 0


@dataclass(frozen=True)
class TooFewForRatios:
    """A session where fewer than RATIO_MIN_TESTS tests are above the floor, so no ratio is printed."""

    measured: int
    below_floor: int
    excluded: int = 0


StepCostReport = StepCostRatios | TooFewForRatios


def step_cost_name(key: str) -> str:
    """The short name of a test used for naming: the part after the last ``::`` of the nodeid, up to RATIO_NAME_CHARS characters. Whitespace and ``;`` become ``_`` (so that
    they are not mistaken for the separators within a line, and the reader does not split a name when reading back)."""
    name = key.rsplit("::", 1)[-1][:RATIO_NAME_CHARS]
    return "".join("_" if char.isspace() or char == ";" else char for char in name)


def step_cost_report(measurements: Iterable[Measurement]) -> StepCostReport:
    """From the call measurements of one session (those whose steps could be measured), find each test's CPU seconds / steps, the median, and the outliers. The denominator is the whole call's steps
    (also for tests judged by world-running, since CPU seconds cannot be split per world segment). Independent of ordering (equal values are decided by name).
    Tests that counted Python calls (``python_calls`` is present; monitoring overhead makes CPU seconds 1.5 to 1.7 times larger) are excluded, and the excluded count is kept."""
    called = tuple(m for m in measurements if m.phase == "call" and m.work is not None)
    worked = tuple(m for m in called if m.python_calls is None)
    excluded = len(called) - len(worked)
    counted = tuple(m for m in worked if m.work is not None and m.work.steps >= RATIO_FLOOR_STEPS)
    below_floor = len(worked) - len(counted)
    if len(counted) < RATIO_MIN_TESTS:
        return TooFewForRatios(measured=len(counted), below_floor=below_floor, excluded=excluded)
    costs = tuple(
        (step_cost_name(m.key), m.judged_cpu_seconds / m.work.steps * 1e6) for m in counted if m.work is not None
    )
    median = statistics.median(cost for _, cost in costs)
    ranked = tuple(
        sorted(
            (StepCost(name, cost, cost / median) for name, cost in costs),
            key=lambda c: (-c.ratio, c.name),
        )
    )
    return StepCostRatios(
        measured=len(counted),
        below_floor=below_floor,
        median=median,
        highest=ranked[0],
        above=tuple(c for c in ranked if c.ratio > RATIO_OUTLIER_FACTOR),
        below=tuple(sorted((c for c in ranked if c.ratio < 1 / RATIO_OUTLIER_FACTOR), key=lambda c: (c.ratio, c.name))),
        excluded=excluded,
    )


def _named_costs(costs: tuple[StepCost, ...]) -> str:
    """Spell out one side of the outliers within one line (empty for 0 tests; the first RATIO_NAMED by name and multiple; the rest only as a count)."""
    if not costs:
        return ""
    shown = "; ".join(f"{c.name} ×{c.ratio:.2f}" for c in costs[:RATIO_NAMED])
    rest = len(costs) - RATIO_NAMED
    return f": {shown}" + (f"; and {rest} more" if rest > 0 else "")


def step_cost_line(report: StepCostReport) -> str:
    """The one ratio line shown in the final summary (per session). Even with 0 outliers, the median and the highest test are shown (so the line's absence can be told apart from "not measured").
    For a run that excluded tests that counted Python calls, the excluded count is written inside the parentheses (not written for 0). Reading the spelling back is
    done by read_step_cost_line."""
    excluded = f"; {RATIO_EXCLUDED_TEXT.format(report.excluded)}" if report.excluded else ""
    head = (
        f"{RATIO_LINE_HEAD}{report.measured} tests with at least {RATIO_FLOOR_STEPS} steps"
        f" ({report.below_floor} below not shown{excluded})"
    )
    match report:
        case TooFewForRatios():
            return f"{head}; fewer than {RATIO_MIN_TESTS} tests, so no ratio is shown"
        case StepCostRatios():
            return (
                f"{head}; median {report.median:.1f}; highest {report.highest.name} ×{report.highest.ratio:.2f}"
                f"; {len(report.above)} above {RATIO_OUTLIER_FACTOR:g}x median{_named_costs(report.above)}"
                f"; {len(report.below)} below 1/{RATIO_OUTLIER_FACTOR:g} of median{_named_costs(report.below)}"
            )


@dataclass(frozen=True)
class StepCostLineRead:
    """Values read back from the ratio line (values after rounding in formatting: the median to 1 decimal, the multiple to 2 decimals, at most RATIO_NAMED named per side).
    For a line that shows no ratio, median and highest are None and outliers are 0."""

    measured: int
    below_floor: int
    median: float | None
    highest: tuple[str, float] | None
    above: int
    above_named: tuple[tuple[str, float], ...]
    below: int
    below_named: tuple[tuple[str, float], ...]
    excluded: int = 0


_NAMED_COST = r"[^\s;]+ ×\d+\.\d{2}"


def _named_side_pattern(side: str) -> str:
    """Regex for the naming of one side of the outliers (``: name ×multiple; ...; and N more``, absent for 0 tests)."""
    # Not an f-string: semgrep's Python parser breaks on (?P<{...}> inside an rf string and the whole file can no longer be measured.
    return "(?:: (?P<" + side + "_names>" + _NAMED_COST + "(?:; " + _NAMED_COST + r")*)(?:; and \d+ more)?)?"


STEP_COST_LINE_PATTERN = re.compile(
    re.escape(RATIO_LINE_HEAD)
    + r"(?P<measured>\d+) tests with at least \d+ steps \((?P<below_floor>\d+) below not shown"
    + "(?:; "
    + re.escape(RATIO_EXCLUDED_TEXT).replace(re.escape("{}"), r"(?P<excluded>\d+)")
    + r")?\)"
    + r"(?:; fewer than \d+ tests, so no ratio is shown"
    + r"|; median (?P<median>\d+\.\d); highest (?P<top>[^\s;]+) ×(?P<top_ratio>\d+\.\d{2})"
    + r"; (?P<above>\d+) above \S+x median"
    + _named_side_pattern("above")
    + r"; (?P<below>\d+) below 1/\S+ of median"
    + _named_side_pattern("below")
    + r")$"
)


def _read_named(text: str | None) -> tuple[tuple[str, float], ...]:
    """Read a list of names (``name ×multiple; name ×multiple``) as pairs of name and multiple."""
    if text is None:
        return ()
    return tuple((name, float(ratio)) for name, ratio in (part.rsplit(" ×", 1) for part in text.split("; ")))


def read_step_cost_line(line: str) -> StepCostLineRead | None:
    """Read the ratio line back (the spelling is defined in step_cost_line; the round-trip test turns any mismatch red). Decoration before the line is allowed.
    None if it is not a ratio line."""
    found = STEP_COST_LINE_PATTERN.search(line)
    if found is None:
        return None
    median = found.group("median")
    return StepCostLineRead(
        measured=int(found.group("measured")),
        below_floor=int(found.group("below_floor")),
        median=float(median) if median is not None else None,
        highest=(found.group("top"), float(found.group("top_ratio"))) if median is not None else None,
        above=int(found.group("above") or 0),
        above_named=_read_named(found.group("above_names")),
        below=int(found.group("below") or 0),
        below_named=_read_named(found.group("below_names")),
        excluded=int(found.group("excluded") or 0),
    )


def judging_line(budgets: Budgets) -> str:
    """At the end of the list, write in one line what this run judged execution by (steps if a step limit exists, otherwise only per-marker seconds)."""
    match budgets.call_steps:
        case int() if isinstance(budgets.phase_log, WorldPhaseLog):
            return "execution is judged by doeff-vm steps (for a test that ran a mock world, only the steps of the world run; CPU seconds are report only)"
        case int():
            return "execution is judged by doeff-vm steps (CPU seconds are report only)"
        case None:
            return f"the execution limit is only per-marker seconds ({MARKER_CALL_BUDGET_INI}); tests with no matching marker are not measured"


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    """At the end, list tests that exceeded the limit, overruns listed in the registry, and registry entries that can be deleted, and print the total steps of tests whose steps could be measured (per test with -v),
    as material for deciding the step limit (#2852, #2853). If any test's steps could be measured, also print the one line of per-test CPU seconds / steps ratio
    (step_cost_line: report only, not part of the verdict, #2854). A run with the --doeff-python-calls marker also prints one line for Python calls."""
    python_calls_summary(terminalreporter, config)
    verdicts = config.stash.get(_VERDICTS_KEY, None)
    if not verdicts:
        return
    budgets = config.stash[_BUDGETS_KEY]
    over = [v for v in verdicts if isinstance(v, OverBudget)]
    registered = [v for v in verdicts if isinstance(v, RegisteredOverBudget)]
    back_in_budget = sorted({v.measurement.key for v in verdicts if isinstance(v, RegisteredWithinBudget)})
    declared_over = [v for v in verdicts if isinstance(v, DeclaredOverBudget)]
    stale_declared = [v for v in verdicts if isinstance(v, StaleDeclaration)]
    worked = [v.measurement for v in verdicts if v.measurement.work is not None]
    if not (over or registered or back_in_budget or declared_over or stale_declared or worked):
        return
    terminalreporter.section("doeff test time limits")
    terminalreporter.line(vm_build_line(budgets.vm_build))
    terminalreporter.line(judging_line(budgets))
    if worked:
        terminalreporter.line(
            f"tests with measured steps: {len(worked)}; total steps {sum(m.work.steps for m in worked if m.work is not None)}"
            " (per test with -v)"
        )
        terminalreporter.line(step_cost_line(step_cost_report(worked)))
        if config.get_verbosity() >= 1:
            for m in sorted(worked, key=lambda m: m.work.steps if m.work is not None else 0, reverse=True):
                terminalreporter.line(f"  {m.key}: {_seconds_text(m)}")
    for verdict in over:
        m = verdict.measurement
        label = "red" if budgets.fails_for(verdict.unit) else "report only"
        terminalreporter.line(
            f"limit exceeded ({label}; limit {_limit_text(verdict.budget, verdict.unit)}): {m.key} ({m.phase} {_seconds_text(m)})"
        )
    for verdict in registered:
        m = verdict.measurement
        terminalreporter.line(f"overrun listed in the registry: {m.key} ({m.phase} {_seconds_text(m)})")
    for key in back_in_budget:
        terminalreporter.line(
            f"registry entry back within the limit: {key}; registry file {registry_file_name(key)} can be deleted"
        )
    for verdict in declared_over:
        m = verdict.measurement
        label = "red" if budgets.fails_for("steps") else "report only"
        terminalreporter.line(
            f"declaration exceeded ({label}; declared steps {verdict.declaration.steps}): {m.key} ({m.phase} {_seconds_text(m)})"
        )
    for verdict in stale_declared:
        m = verdict.measurement
        terminalreporter.line(
            f"stale declaration (at most {STALE_RATIO:g} times declared steps {verdict.declaration.steps}): {m.key} ({m.phase} {_seconds_text(m)})"
        )


def called_tests(verdicts: Sequence[Verdict]) -> tuple[CalledTest, ...]:
    """Among the judged tests, the count and steps of tests that counted Python calls (passed Hy tests that declare steps)."""
    return tuple(
        CalledTest(v.measurement.key, v.measurement.python_calls, v.measurement.work.steps)
        for v in verdicts
        if v.measurement.python_calls is not None and v.measurement.work is not None
    )


def python_calls_summary(terminalreporter: pytest.TerminalReporter, config: pytest.Config) -> None:
    """At the end of a run with the --doeff-python-calls marker, print one line with the total calls of the counted tests (per test with -v), or one line giving the reason nothing was counted
    (a run without the marker prints nothing; the daily report includes the line only for the run with the marker). Counted tests are read from the judged tests (passed Hy tests)."""
    python_calls = config.stash.get(_PYTHON_CALLS_KEY, None)
    if python_calls is None:
        return
    terminalreporter.section("doeff test Python calls")
    match python_calls:
        case NotCounted() as not_counted:
            terminalreporter.line(unmeasurable_line(not_counted))
        case CallCounter(tool_id=tool_id):
            tests = called_tests(config.stash.get(_VERDICTS_KEY, []))
            terminalreporter.line(summary_line(tests))
            terminalreporter.line(f"counted with sys.monitoring tool id {tool_id}, only during the call of tests that declare steps (collection, setup, and teardown are not counted)")
            if config.get_verbosity() >= 1:
                for test in sorted(tests, key=lambda test: test.calls, reverse=True):
                    terminalreporter.line(called_test_line(test))
