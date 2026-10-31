"""印の秒の上限を process の木の CPU 秒で判じる宣言(declare_process_tree_cpu・doeff_hy_pytest._tree_cpu)の検。

確かめる事: 宣言した検は、壁の時計が上限を越えても木の CPU 秒が内なら緑(機体の混みで赤にならない)・子孫が CPU を使って上限を
越えれば赤(子の CPU も数える)・CPU を使わずに安全弁の壁の時計を越えれば赤・宣言しない検は今までどおり壁の時計。読みは刈り取って
いない子孫の CPU も /proc から数える。
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
    unmeasurable_reason() is not None, reason="/proc の stat を読めない機体"
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


DECLARED = TreeCpuDeclaration("重さは子の型検査の CPU に在り、外を待たない")


def test_a_declared_test_stretched_by_the_machine_load_is_judged_by_its_tree_cpu() -> None:
    """日次 10-22 04:00 の形: 壁の時計 33 秒(上限 30 秒を越える)・木の CPU 21 秒の宣言した検は緑。宣言しない同じ検は壁の時計で赤。"""
    measured = _measured(33.0, TreeCpu(0.96, 20.0, 12))
    assert isinstance(
        judge_call(_budgets(), ["real_world"], measured, None, DECLARED), WithinBudget
    )
    assert judge_call(_budgets(), ["real_world"], measured, None) == OverBudget(
        measured, 30.0, "wall-seconds"
    )


def test_a_declared_test_whose_descendants_use_more_cpu_than_the_budget_is_red() -> None:
    """子孫の CPU も数える: 自分 1 秒・子孫 31 秒の宣言した検は、壁の時計が上限の内でも木の CPU 秒で赤。"""
    measured = _measured(20.0, TreeCpu(1.0, 31.0, 3))
    assert judge_call(_budgets(), ["real_world"], measured, None, DECLARED) == OverBudget(
        measured, 30.0, "tree-cpu-seconds"
    )


def test_a_declared_test_that_waits_past_the_wall_backstop_is_red() -> None:
    """CPU を使わずに待った検は安全弁で赤: 木の CPU 1 秒・壁の時計が上限 × 倍率を越えた宣言した検。"""
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
    """木を読めない機体(macOS)では、宣言した検も壁の時計の上限で判じる(今までより緩くならない)。"""
    measured = _measured(33.0, TreeCpuUnmeasured("/proc を読めない"))
    assert judge_call(_budgets(), ["real_world"], measured, None, DECLARED) == OverBudget(
        measured, 30.0, "wall-seconds"
    )


def test_declare_process_tree_cpu_attaches_the_reason_and_refuses_an_empty_or_multiline_one() -> (
    None
):
    def a_test() -> None: ...

    assert declared_tree_cpu_of(a_test) is None
    declare_process_tree_cpu(a_test, "  子の型検査の CPU  ")
    assert declared_tree_cpu_of(a_test) == TreeCpuDeclaration("子の型検査の CPU")
    for bad in ("", "  ", "1 行目\n2 行目"):
        with pytest.raises(ValueError, match="理由は空でない 1 行"):
            declare_process_tree_cpu(a_test, bad)


def test_parse_stat_reads_the_parent_and_cpu_after_the_last_parenthesis() -> None:
    """名の欄に空白と括弧が在っても、最後の閉じ括弧の後ろを数える(utime・stime は clock tick)。"""
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
    """始めに居た子孫は始めの値からの差・後から生まれた子孫は 0 からの値。"""
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
    """刈り取っていない子(読みを止めた時にまだ生きている子)の CPU も /proc の読みで数える — RUSAGE_CHILDREN には出ない分。"""
    sampler = TreeCpuSampler(tick_seconds=0.02)
    assert sampler.start() is None
    child = subprocess.Popen(
        [sys.executable, "-c", SPIN_THEN_SLEEP.format(spin=0.4, sleep=30)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        # 子が CPU を回し終えた合図の 1 行を待つ(子はその後 眠ったまま生きている — 刈り取らずに読みを止める)。
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
    'markers = ["real_world: 本物の I/O"]\ndoeff_test_call_budget_by_marker = ["real_world=2.0"]\n'
    'doeff_test_budget_mode = "fail"\n'
)


def _child_test(name: str, child_code: str, declared: bool) -> str:
    declaration = f'(declare-process-tree-cpu {name} "重さは子の CPU に在る")\n' if declared else ""
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
    """走りの中で: 子が 2.3 秒 CPU を回す宣言した検は木の CPU 秒で赤(子の CPU を数える)、子が 2.3 秒眠る宣言した検は壁の時計が
    上限 2 秒を越えても木の CPU 秒が内で緑(安全弁 4 秒の内)、同じく眠る宣言しない検は壁の時計で赤。"""
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
            "*test_burn.hy::test_burns_in_a_child の実行(call)が 壁の時計 *process の木の CPU * 秒(自分 * 秒・子孫 * 本 * 秒)*"
            "上限 process の木の CPU 2.000 秒*",
            "*test_plain.hy::test_naps_undeclared の実行(call)が 壁の時計 *上限 壁の時計 2.000 秒*",
        ]
    )
    assert "test_naps_in_a_child の実行" not in out
