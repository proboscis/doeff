"""Tests for the declaration that judges a marker's seconds limit by process-tree CPU seconds (declare_process_tree_cpu, doeff_hy_pytest._tree_cpu).

What it checks: a declared test is green when tree CPU seconds are within the limit even if wall clock exceeds it (load does not turn it red); red when descendants use CPU and exceed the limit
(child CPU is counted too); red when it uses no CPU but exceeds the wall-clock safety valve; an undeclared test is judged by wall clock as before. Reads also count
the CPU of unreaped descendants from /proc.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from doeff_hy_pytest._budget import (
    TREE_CPU_WALL_BACKSTOP_FACTOR,
    Budgets,
    CompileTally,
    Measurement,
    OverBudget,
    TreeCpuDeclaration,
    UncheckedVmBuild,
    WithinBudget,
    declare_process_tree_cpu,
    declared_tree_cpu_of,
    judge_call,
)
from doeff_hy_pytest._tree_cpu import (
    ProcessStat,
    TreeCpu,
    TreeCpuSampler,
    TreeCpuUnmeasured,
    descendants_cpu,
    descendants_of,
    parse_stat,
    unmeasurable_reason,
)

pytest_plugins = ["pytester"]

LINUX_PROC = pytest.mark.skipif(
    unmeasurable_reason() is not None, reason="a machine where /proc stat cannot be read"
)


def _budgets() -> Budgets:
    return Budgets(
        call_seconds_by_marker={"real_world": 30.0},
        collect_seconds=None,
        mode="fail",
        registry={},
        registry_dirs=(),
        vm_build=UncheckedVmBuild(),
    )


def _measured(wall: float, tree: TreeCpu | TreeCpuUnmeasured | None) -> Measurement:
    return Measurement(
        key="t.hy::test",
        phase="call",
        cpu_seconds=0.5,
        wall_seconds=wall,
        compile=CompileTally(0, 0.0, 0.0),
        tree=tree,
    )


DECLARED = TreeCpuDeclaration("the weight is the CPU of a child type check, and it does not wait on anything outside")


def test_a_declared_test_stretched_by_the_machine_load_is_judged_by_its_tree_cpu() -> None:
    """The shape of the daily run 10-22 04:00: a declared test with wall clock 33 s (over the 30 s limit) and tree CPU 21 s is green. The same test undeclared is red by wall clock."""
    measured = _measured(33.0, TreeCpu(0.96, 20.0, 12))
    assert isinstance(
        judge_call(_budgets(), ["real_world"], measured, None, DECLARED), WithinBudget
    )
    assert judge_call(_budgets(), ["real_world"], measured, None) == OverBudget(
        measured, 30.0, "wall-seconds"
    )


def test_a_declared_test_whose_descendants_use_more_cpu_than_the_budget_is_red() -> None:
    """Descendant CPU is counted too: a declared test with own 1 s and descendants 31 s is red by tree CPU seconds even if wall clock is within the limit."""
    measured = _measured(20.0, TreeCpu(1.0, 31.0, 3))
    assert judge_call(_budgets(), ["real_world"], measured, None, DECLARED) == OverBudget(
        measured, 30.0, "tree-cpu-seconds"
    )


def test_a_declared_test_that_waits_past_the_wall_backstop_is_red() -> None:
    """A test that waited without using CPU is red by the safety valve: a declared test with tree CPU 1 s and wall clock over limit x factor."""
    backstop = 30.0 * TREE_CPU_WALL_BACKSTOP_FACTOR
    measured = _measured(backstop + 1.0, TreeCpu(0.5, 0.5, 1))
    assert judge_call(_budgets(), ["real_world"], measured, None, DECLARED) == OverBudget(
        measured, backstop, "wall-seconds"
    )
    assert isinstance(
        judge_call(
            _budgets(), ["real_world"], _measured(backstop, TreeCpu(0.5, 0.5, 1)), None, DECLARED
        ),
        WithinBudget,
    )


def test_a_declared_test_whose_tree_cannot_be_read_stays_on_the_wall_clock() -> None:
    """On a machine that cannot read the tree (macOS), a declared test is also judged by the wall-clock limit (no looser than before)."""
    measured = _measured(33.0, TreeCpuUnmeasured("cannot read /proc"))
    assert judge_call(_budgets(), ["real_world"], measured, None, DECLARED) == OverBudget(
        measured, 30.0, "wall-seconds"
    )


def test_declare_process_tree_cpu_attaches_the_reason_and_refuses_an_empty_or_multiline_one() -> (
    None
):
    def a_test() -> None: ...

    assert declared_tree_cpu_of(a_test) is None
    declare_process_tree_cpu(a_test, "  child type-check CPU  ")
    assert declared_tree_cpu_of(a_test) == TreeCpuDeclaration("child type-check CPU")
    for bad in ("", "  ", "line 1\nline 2"):
        with pytest.raises(ValueError, match=": '"):
            declare_process_tree_cpu(a_test, bad)


def test_parse_stat_reads_the_parent_and_cpu_after_the_last_parenthesis() -> None:
    """Even if the name field contains spaces and parentheses, count after the last closing parenthesis (utime and stime are clock ticks)."""
    import os

    ticks = os.sysconf("SC_CLK_TCK")
    fields = ["S", "77"] + ["0"] * 9 + [str(3 * ticks), str(ticks)] + ["0"] * 30
    stat = parse_stat("4242 (a) (b c) " + " ".join(fields))
    assert stat == ProcessStat(parent=77, cpu_seconds=4.0)


def test_descendants_are_followed_through_every_generation_and_exclude_the_root() -> None:
    parents = {10: 1, 11: 10, 12: 11, 13: 10, 20: 1, 21: 20}
    assert descendants_of(10, parents) == frozenset({11, 12, 13})
    assert descendants_of(99, parents) == frozenset()


def test_descendants_cpu_counts_a_process_born_during_the_call_from_zero() -> None:
    """A descendant present at the start is the difference from its initial value; one born later is the value from 0."""
    assert descendants_cpu({11: 2.0}, {11: 2.5, 12: 1.25}) == pytest.approx(1.75)


SPIN_THEN_SLEEP = (
    "import time\n"
    "t0 = time.process_time()\n"
    "while time.process_time() - t0 < {spin}: pass\n"
    "print('spun', flush=True)\n"
    "time.sleep({sleep})\n"
)


@LINUX_PROC
def test_the_sampler_counts_a_descendant_that_has_not_been_reaped() -> None:
    """The CPU of an unreaped child (a child still alive when reading stops) is also counted from /proc reads - the share that does not show in RUSAGE_CHILDREN."""
    sampler = TreeCpuSampler(tick_seconds=0.02)
    assert sampler.start() is None
    child = subprocess.Popen(
        [sys.executable, "-c", SPIN_THEN_SLEEP.format(spin=0.4, sleep=30)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        # Wait for the one-line signal that the child finished spinning the CPU (after that the child stays alive asleep - reading stops without reaping it).
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "spun"
        tree = sampler.stop()
    finally:
        child.kill()
        child.wait()
    assert isinstance(tree, TreeCpu), tree
    assert tree.descendants_seconds >= 0.35, tree
    assert tree.processes >= 1, tree


@LINUX_PROC
def test_the_sampler_does_not_count_sleeping_as_cpu() -> None:
    sampler = TreeCpuSampler(tick_seconds=0.02)
    assert sampler.start() is None
    subprocess.run([sys.executable, "-c", "import time; time.sleep(0.5)"], check=True)
    tree = sampler.stop()
    assert isinstance(tree, TreeCpu), tree
    assert tree.seconds < 0.4, tree


CONFTEST = """\
import pytest


@pytest.fixture
def doeff_interpreter():
    def run_program(program, *, env=None):
        from doeff import run

        return run(program)

    return run_program
"""

INI = (
    'markers = ["real_world: real I/O"]\ndoeff_test_call_budget_by_marker = ["real_world=2.0"]\n'
    'doeff_test_budget_mode = "fail"\n'
)


def _child_test(name: str, child_code: str, declared: bool) -> str:
    declaration = f'(declare-process-tree-cpu {name} "the weight is in child CPU")\n' if declared else ""
    return (
        "(require doeff-hy.macros [deftest])\n"
        "(import pytest subprocess sys)\n"
        "(import doeff_hy_pytest [declare-process-tree-cpu])\n"
        "(setv pytestmark [pytest.mark.real_world])\n"
        f"(deftest {name}\n"
        f'  (subprocess.run [sys.executable "-c" {json.dumps(child_code)}] :check True)\n'
        "  (assert True))\n" + declaration
    )


def _project(pytester: pytest.Pytester, files: dict[str, str]) -> None:
    pytester.makeconftest(CONFTEST)
    pytester.makepyprojecttoml(
        '[tool.pytest.ini_options]\ndoeff_adr_hy_files = ["test_*.hy"]\n' + INI
    )
    pytester.makefile(".hy", **files)


@LINUX_PROC
def test_in_a_run_a_declared_test_is_judged_by_the_cpu_of_its_child(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In a run: a declared test whose child spins CPU for 2.3 s is red by tree CPU seconds (child CPU is counted); a declared test whose child sleeps 2.3 s is green because wall clock
    exceeds the 2 s limit but tree CPU seconds are within it (inside the 4 s safety valve); an undeclared test that sleeps the same is red by wall clock."""
    from doeff._vm import native as vm_ext

    monkeypatch.setattr(vm_ext, "_invariant_checks_enabled", lambda: False)
    _project(
        pytester,
        {
            "test_burn": _child_test(
                "test-burns-in-a-child", SPIN_THEN_SLEEP.format(spin=2.3, sleep=0), True
            ),
            "test_nap": _child_test("test-naps-in-a-child", "import time; time.sleep(2.3)", True),
            "test_plain": _child_test(
                "test-naps-undeclared", "import time; time.sleep(2.3)", False
            ),
        },
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1, failed=2)
    out = result.stdout.str()
    result.stdout.fnmatch_lines(
        [
            "*test_burn.hy::test_burns_in_a_child*(call)*2.000*",
            "*test_plain.hy::test_naps_undeclared*(call)*2.000*",
        ]
    )
    assert not any("test_naps_in_a_child" in line and "(call)" in line for line in out.splitlines())
