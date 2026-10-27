"""Python の呼び出しの数え(doeff_hy_pytest/python_calls.py・agora-redesign #2854 の受入条 c4c2e04dc)の検。

依頼書 B の受入条と失敗例を 1 つずつ潰す: 印の無い走りは枠を取らず set_events を呼ばない(受入条 2)・数えるのは歩数を宣言した
検の call の間だけ(受入条 3・決定 3)・複数 thread で数を落とさない(受入条 4)・枠は 4 → 5 → 3 の順で 0〜2 を取らず、全部
埋まっていれば「測れない」の 1 行で走りは落ちない(決定 5)。

歩数は test_budget.py と同じく偽の数の口(fake_work)で足す。sys.monitoring の枠を偽の持ち主で埋める検は、doeff-hy の
called_code が process に 1 度だけ決める枠(埋まっていれば拾わない形に倒れたまま残る)を後の検に持ち越さないよう、別の process で
走らせる。
"""

from __future__ import annotations

import re
import sys
import threading

import pytest
from doeff_hy_pytest.budget import (
    RATIO_EXCLUDED_TEXT,
    RATIO_MIN_TESTS,
    CompileTally,
    Measurement,
    StepCostRatios,
    TooFewForRatios,
    VmWork,
    read_step_cost_line,
    step_cost_line,
    step_cost_report,
)
from doeff_hy_pytest.python_calls import (
    TOOL_NAME,
    CallCounter,
    CalledTest,
    FreeTool,
    NotCounted,
    choose_tool,
    summary_line,
    unmeasurable_line,
)

pytest_plugins = ["pytester"]

# doeff-hy の called_code が取る枠の持ち主の名(doeff_hy_bytecode_guard/called_code.py)。
DOEFF_HY_TOOL = "doeff-hy-called-code"

# sys.monitoring の枠を取って数える検の印 — sys.monitoring は Python 3.12 から。3.11 の走りは数えずに「測れない」と出す
# (test_before_3_12_the_flag_reports_unmeasurable が見る)。
needs_monitoring = pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring は Python 3.12 から")


@pytest.fixture(autouse=True)
def _own_stores(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """子の Hy の code の置き場と doeff-adr の収集の記録を検ごとに空にする(test_budget.py の同じ名の fixture の註)。"""
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", str(tmp_path_factory.mktemp("code-store")))
    monkeypatch.delenv("DOEFF_ADR_ITEMS_CACHE", raising=False)


CONFTEST = """\
import pytest

import doeff_vm.doeff_vm as _ext
import fake_work

_ext._invariant_checks_enabled = lambda: False
_ext._vm_work_counts = fake_work.counts


@pytest.fixture
def doeff_interpreter():
    def run_program(program, *, env=None):
        from doeff import run

        return run(program)

    return run_program
"""

FAKE_WORK = """\
STEPS = [0]


def add(steps):
    STEPS[0] += steps


def counts():
    return (STEPS[0], 0)


def noop():
    return None


def call_many(count):
    for _ in range(count):
        noop()
"""

# 宣言した検の本体の呼び出し(BODY_CALLS 回)と、数えてはいけない区間(module の頭 = 収集・fixture = setup と teardown)の呼び出し
# (NOISE_CALLS 回ずつ)。数えが区間を漏らせば合計が NOISE_CALLS を越える。
BODY_CALLS = 20_000
NOISE_CALLS = 300_000
THREADS = 4
CALLS_PER_THREAD = 50_000

DECLARED = f"""\
(require doeff-hy.macros [deftest])
(import pytest threading fake_work)
(import doeff_hy_pytest.budget [declare-step-budget])
(fake_work.call-many {NOISE_CALLS})
(defn [pytest.fixture] noisy []
  (fake_work.call-many {NOISE_CALLS})
  (yield None)
  (fake_work.call-many {NOISE_CALLS}))
(deftest test-declared [noisy]
  (fake_work.add 1000)
  (fake_work.call-many {BODY_CALLS})
  (assert True))
(declare-step-budget test-declared 1500 "検の例")
(deftest test-threaded
  (fake_work.add 1000)
  (setv threads (lfor _ (range {THREADS}) (threading.Thread :target fake_work.call-many :args #({CALLS_PER_THREAD}))))
  (for [t threads] (.start t))
  (for [t threads] (.join t))
  (assert True))
(declare-step-budget test-threaded 1500 "検の例")
(deftest test-undeclared
  (fake_work.add 10)
  (fake_work.call-many {NOISE_CALLS})
  (assert True))
"""

STEPS_INI = 'doeff_test_budget_mode = "fail"\ndoeff_test_call_budget_steps = "100000"\n'


def _project(pytester: pytest.Pytester, conftest_extra: str = "") -> None:
    pytester.makepyfile(fake_work=FAKE_WORK)
    pytester.makeconftest(CONFTEST + conftest_extra)
    pytester.makepyprojecttoml('[tool.pytest.ini_options]\ndoeff_adr_hy_files = ["test_*.hy"]\n' + STEPS_INI)
    pytester.makefile(".hy", test_declared=DECLARED)


def _counted(result: pytest.RunResult, key: str) -> int:
    """-v の検ごとの行から、その検の呼び出しの数を読む。"""
    pattern = re.compile(rf"^  \S*{re.escape(key)}: (\d+) 回・歩数 (\d+)・")
    found = [int(m.group(1)) for line in result.stdout.lines if (m := pattern.match(line))]
    assert len(found) == 1, result.stdout.str()
    return found[0]


# ── 純粋な部分 ─────────────────────────────────────────────────────────────────────────────


def test_the_tool_is_chosen_four_then_five_then_three_and_never_zero_to_two() -> None:
    """枠は 4 → 5 → 3 の順に空きを取る。0〜2(debugger・coverage・profiler)は空いていても取らず、3〜5 が埋まれば理由つきで数えない。"""
    owners: dict[int, str] = {}
    assert choose_tool(owners.get) == FreeTool(4)
    owners[4] = "偽の持ち主"
    assert choose_tool(owners.get) == FreeTool(5)
    owners[5] = "optimizer"
    owners[3] = "doeff-hy-called-code"
    assert choose_tool(owners.get) == NotCounted(
        "sys.monitoring の枠が全部使用中: 4=偽の持ち主・5=optimizer・3=doeff-hy-called-code"
    ), "0〜2 しか空いていないのに枠を選んだ"
    del owners[3]
    assert choose_tool(owners.get) == FreeTool(3)


def test_the_summary_line_names_the_total_the_steps_the_ratio_and_the_top_three() -> None:
    """終わりの 1 行: 数えた検の数・呼び出しの合計・歩数・1 歩あたり・多い順の上位 3 本。検の無い時も黙らず 0 本と出す。"""
    tests = [CalledTest("a.hy::t1", 100, 10), CalledTest("a.hy::t2", 900, 20), CalledTest("b.hy::t3", 300, 30), CalledTest("b.hy::t4", 50, 40)]
    assert summary_line(tests) == (
        "Python の呼び出し(sys.monitoring CALL): 歩数を宣言した検 4 本で 1350 回・歩数 100・1 歩あたり 13.5 回・"
        "多い順(上位 3): a.hy::t2 900・b.hy::t3 300・a.hy::t1 100"
    )
    assert summary_line([]) == (
        "Python の呼び出し(sys.monitoring CALL): 歩数を宣言した検 0 本で 0 回・歩数 0・1 歩あたり — 回・多い順(上位 3): なし"
    )
    assert unmeasurable_line(NotCounted("理由")) == "python-calls: 測れない(理由)"


def _cost(name: str, micros_per_step: float, python_calls: int | None = None) -> Measurement:
    """歩数 2 万・CPU 秒 = µs/歩 × 2 万の call の測り(python_calls = 数えた呼び出しの数・数えない検は None)。"""
    return Measurement(
        key=f"t.hy::{name}",
        phase="call",
        cpu_seconds=micros_per_step * 20_000 / 1e6,
        wall_seconds=1.0,
        compile=CompileTally(0, 0.0, 0.0),
        work=VmWork(20_000, 0),
        python_calls=python_calls,
    )


def test_tests_whose_python_calls_were_counted_are_left_out_of_the_cpu_per_step_ratio() -> None:
    """受入条 5: 呼び出しを数えた検(監視の上乗せで CPU 秒が 1.5〜1.7 倍)は依頼書 A の CPU 秒 ÷ 歩数の母集団に入れず、除いた数を
    比の行に書き、読み戻せる。数えた検が 30 µs/歩でも「最も高い」・外れに出ない。数えた検の無い走りの行は前の綴りのまま。"""
    plain = tuple(_cost(f"test_{i:02d}", 10.0) for i in range(RATIO_MIN_TESTS))
    report = step_cost_report((*plain, _cost("test_watched_a", 30.0, 400_000), _cost("test_watched_b", 25.0, 0)))
    assert isinstance(report, StepCostRatios)
    assert report.measured == RATIO_MIN_TESTS
    assert report.excluded == 2
    assert report.above == ()
    line = step_cost_line(report)
    assert f"(未満 0 本は出さない・{RATIO_EXCLUDED_TEXT.format(2)})" in line
    read = read_step_cost_line(line)
    assert read is not None
    assert read.excluded == 2
    assert read.measured == RATIO_MIN_TESTS
    unwatched = step_cost_report(plain)
    assert step_cost_line(unwatched).startswith(f"CPU 秒 ÷ 歩数(µs/歩): 歩数 10000 以上の {RATIO_MIN_TESTS} 本(未満 0 本は出さない)・")
    unwatched_read = read_step_cost_line(step_cost_line(unwatched))
    assert unwatched_read is not None
    assert unwatched_read.excluded == 0
    few = step_cost_report((plain[0], _cost("test_watched", 30.0, 10)))
    assert few == TooFewForRatios(measured=1, below_floor=0, excluded=1)
    few_read = read_step_cost_line(step_cost_line(few))
    assert few_read is not None
    assert few_read.excluded == 1


# ── sys.monitoring に触る部分 ──────────────────────────────────────────────────────────────────


def _noop() -> None:
    return None


def _call_many(count: int) -> None:
    for _ in range(count):
        _noop()


@needs_monitoring
def test_the_counter_does_not_drop_calls_across_threads() -> None:
    """4 thread × 5 万回の呼び出しを数え落とさない(free-threaded の Python で callback の `+= 1` は 6 割近く落とした — 実測 5)。
    数え終えたら枠を返す。"""
    counter = CallCounter.acquire()
    assert isinstance(counter, CallCounter), counter
    try:
        assert sys.monitoring.get_tool(counter.tool_id) == TOOL_NAME
        counter.start()
        threads = [threading.Thread(target=_call_many, args=(CALLS_PER_THREAD,)) for _ in range(THREADS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        calls = counter.stop()
    finally:
        counter.release()
    assert calls >= THREADS * CALLS_PER_THREAD
    assert sys.monitoring.get_tool(counter.tool_id) is None


# ── pytest の走り ─────────────────────────────────────────────────────────────────────────────


@needs_monitoring
def test_counts_only_the_call_of_a_declared_test_and_not_an_undeclared_one(pytester: pytest.Pytester) -> None:
    """印を付けた走り: 歩数を宣言した検の call の呼び出しだけを数える — 収集(module の頭)・setup・teardown(fixture)の
    30 万回ずつと、宣言の無い検の 30 万回は数えない。thread 4 本の検は下限を割らない。"""
    _project(pytester)
    result = pytester.runpytest("-v", "--doeff-python-calls")
    result.assert_outcomes(passed=3)
    declared = _counted(result, "test_declared.hy::test_declared")
    assert BODY_CALLS <= declared < NOISE_CALLS
    assert _counted(result, "test_declared.hy::test_threaded") >= THREADS * CALLS_PER_THREAD
    assert not any(re.match(r"^  \S*test_undeclared: \d+ 回・歩数", line) for line in result.stdout.lines)
    result.stdout.fnmatch_lines(
        [
            "*Python の呼び出し(sys.monitoring CALL): 歩数を宣言した検 2 本で * 回・歩数 2000・1 歩あたり * 回・"
            "多い順(上位 3): test_declared.hy::test_threaded *・test_declared.hy::test_declared *",
            "*sys.monitoring の枠 4 番で*",
        ]
    )


@needs_monitoring
def test_without_the_flag_no_tool_is_taken_and_set_events_is_never_called(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """印の無い走り(既定): 枠を 1 つも取らず、doeff-hy 以外の枠に set_events を 1 回も呼ばない(毎回の検の CPU を変えない)。
    走りの前後で全部の枠の持ち主が同じ。終わりに呼び出しの行を出さない。"""
    _project(pytester)
    monitoring = sys.monitoring
    owners_before = tuple(monitoring.get_tool(tool) for tool in range(6))
    use_tool_id = monitoring.use_tool_id
    set_events = monitoring.set_events

    def refusing_use_tool_id(tool: int, name: str) -> None:
        """doeff-hy の called_code 以外が枠を取ったら、その場で走りを落とす。"""
        if name != DOEFF_HY_TOOL:
            raise AssertionError(f"印の無い走りで枠 {tool} を取った: {name}")
        use_tool_id(tool, name)

    def refusing_set_events(tool: int, events: int) -> None:
        """doeff-hy の called_code 以外の枠に事象を入れ外ししたら、その場で走りを落とす。"""
        if monitoring.get_tool(tool) != DOEFF_HY_TOOL:
            raise AssertionError(f"印の無い走りで枠 {tool}({monitoring.get_tool(tool)})に set_events を呼んだ")
        set_events(tool, events)

    monkeypatch.setattr(monitoring, "use_tool_id", refusing_use_tool_id)
    monkeypatch.setattr(monitoring, "set_events", refusing_set_events)
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=3)
    # 走りの中で doeff-hy が初めて Hy を変換すれば、空いた枠を 1 つ取りうる(前から在る持ち主はそのまま)。
    owners_after = tuple(monitoring.get_tool(tool) for tool in range(6))
    assert all(
        after == before or (before is None and after == DOEFF_HY_TOOL)
        for before, after in zip(owners_before, owners_after, strict=True)
    ), (owners_before, owners_after)
    assert TOOL_NAME not in owners_after
    assert "Python の呼び出し" not in result.stdout.str()
    assert "python-calls: " not in result.stdout.str()


OCCUPY = """
import sys

for _tool in {tools}:
    if sys.monitoring.get_tool(_tool) is None:
        sys.monitoring.use_tool_id(_tool, "偽の持ち主")
"""


@needs_monitoring
def test_a_taken_tool_four_moves_the_counter_to_five(pytester: pytest.Pytester) -> None:
    """4 番を偽の持ち主が持つ時は 5 番を取って数える(固定の番号で取ると、使用中の枠で pytest が起動で落ちた — 失敗例)。"""
    _project(pytester, OCCUPY.format(tools=(4,)))
    result = pytester.runpytest_subprocess("-v", "-p", "no:cacheprovider", "--doeff-python-calls")
    result.assert_outcomes(passed=3)
    result.stdout.fnmatch_lines(["*歩数を宣言した検 2 本で*", "*sys.monitoring の枠 5 番で*"])


@needs_monitoring
def test_all_tools_taken_reports_unmeasurable_and_the_run_does_not_fail(pytester: pytest.Pytester) -> None:
    """3〜5 番が全部埋まっていれば数えず、「測れない」と持ち主を 1 行出し、走りは落とさない(0〜2 番へは倒れない)。"""
    _project(pytester, OCCUPY.format(tools=(3, 4, 5)))
    result = pytester.runpytest_subprocess("-v", "-p", "no:cacheprovider", "--doeff-python-calls")
    result.assert_outcomes(passed=3)
    result.stdout.fnmatch_lines(["*python-calls: 測れない(sys.monitoring の枠が全部使用中: 4=偽の持ち主・5=*・3=*)*"])
    assert "歩数を宣言した検" not in result.stdout.str()


@pytest.mark.skipif(sys.version_info >= (3, 12), reason="sys.monitoring の在る版の数えは上の検が見る")
def test_before_3_12_the_flag_reports_unmeasurable(pytester: pytest.Pytester) -> None:
    """sys.monitoring の無い版(3.11)の印を付けた走りは、数えずに「測れない」と訳を 1 行出し、走りは落とさない。"""
    _project(pytester)
    result = pytester.runpytest("-v", "--doeff-python-calls")
    result.assert_outcomes(passed=3)
    result.stdout.fnmatch_lines(["*python-calls: 測れない(sys.monitoring が無い(Python 3.11))*"])
    assert "歩数を宣言した検" not in result.stdout.str()


def test_the_flag_without_budget_settings_reports_unmeasurable(pytester: pytest.Pytester) -> None:
    """印を付けたのに上限の設定が無い repo(歩数を測らない)は、黙らず「測れない」の 1 行を出す。"""
    pytester.makepyfile(test_plain="def test_plain():\n    assert True\n")
    result = pytester.runpytest("-q", "--doeff-python-calls")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*python-calls: 測れない(doeff の検の上限の設定が無い — 歩数を測らない)*"])
