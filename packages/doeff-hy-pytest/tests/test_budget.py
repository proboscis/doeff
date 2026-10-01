"""Hy の検の時間の上限(doeff_hy_pytest/budget.py)の検。

時間は CPU 時間で測るので、検の file の中は sleep ではなく CPU を回して時間を使う。上限は小さく(0.05 秒)、
超える側は 0.3 秒回して、機体の揺れで合否が入れ替わらない幅を取る。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from doeff_hy_pytest.budget import (
    Budgets,
    CompileCounter,
    CompileTally,
    ImportTally,
    Measurement,
    OverBudget,
    RegisteredOverBudget,
    RegisteredWithinBudget,
    RegistryError,
    SettingError,
    UncheckedVmBuild,
    Verdict,
    WithinBudget,
    call_budget_for,
    judge,
    load_registries,
    load_registry,
    parse_marker_budgets,
    registry_file_name,
)

pytest_plugins = ["pytester"]

CONFTEST = """\
import pytest


@pytest.fixture
def doeff_interpreter():
    def run_program(program, *, env=None):
        from doeff import run

        return run(program)

    return run_program
"""

# CPU を回して時間を使う(sleep は CPU 時間に出ない)。
SPIN = "(import time) (defn spin [seconds] (setv t0 (time.process_time)) (while (< (- (time.process_time) t0) seconds) (setv _ None)))"

SLOW_CALL = f"""\
(require doeff-hy.macros [deftest])
{SPIN}
(deftest test-slow
  (spin 0.3)
  (assert True))
(deftest test-fast
  (assert True))
"""

SLOW_COLLECT = f"""\
(require doeff-hy.macros [deftest])
{SPIN}
(spin 0.3)
(deftest test-fast
  (assert True))
"""

# macro の展開(変換の中)で CPU を回す — キャッシュ無しの変換の重さの代わり。
SLOW_COMPILE = f"""\
(require doeff-hy.macros [deftest])
(defmacro slow-expansion []
  {SPIN}
  (spin 0.3)
  1)
(setv VALUE (slow-expansion))
(deftest test-compiled
  (assert (= VALUE 1)))
"""


def _project(pytester: pytest.Pytester, ini: str, files: dict[str, str]) -> None:
    pytester.makeconftest(CONFTEST)
    pytester.makepyprojecttoml(
        '[tool.pytest.ini_options]\ndoeff_adr_hy_files = ["test_*.hy"]\n' + ini
    )
    pytester.makefile(".hy", **files)


def _measurement(cpu: float, compile_cpu: float = 0.0, key: str = "t.hy::test") -> Measurement:
    return Measurement(
        key=key,
        phase="call",
        cpu_seconds=cpu,
        wall_seconds=cpu,
        compile=CompileTally(1 if compile_cpu else 0, compile_cpu),
    )


def test_judge_subtracts_the_compile_time() -> None:
    """区間の中の変換(キャッシュ無し)の CPU 秒は引いて判定する — キャッシュ無しを赤にしない。"""
    assert isinstance(judge(_measurement(5.0, compile_cpu=4.5), 1.0, {}), WithinBudget)
    assert isinstance(judge(_measurement(5.0, compile_cpu=3.0), 1.0, {}), OverBudget)


def test_compile_counter_adds_only_the_outermost_window(tmp_path, monkeypatch) -> None:
    """変換の中の import の変換(Hy の require)は二重に足さない。キャッシュに当たる import は数えない。"""
    import importlib
    import sys

    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    # 初回の import が本当にキャッシュ無しの変換になるよう、doeff-hy の作業木をまたぐ code の置き場(環境変数
    # DOEFF_HY_CODE_STORE — doeff_hy_bytecode_guard/loader_hooks.py)をこの検の空の dir にする。既定の利用者の cache の
    # 置き場は process をまたいで残るので、同じ中身のこの module を 2 度目に走らせると置き場に当たって変換が 0 回になる。
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", str(tmp_path / "code-store"))
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "budget_inner_mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "budget_outer_mod.hy").write_text(
        "(defmacro load-inner [] (import budget-inner-mod) 1)\n(setv VALUE (load-inner))\n",
        encoding="utf-8",
    )
    counter = CompileCounter()
    counter.install()
    try:
        import hy  # noqa: F401 - Hy の import の口を差し込む

        before = counter.tally()
        imports_before = counter.import_tally()
        importlib.import_module("budget_outer_mod")
        cold = counter.tally().since(before)
        # 最も外側の区間は budget_outer_mod の初回の import なので、中の変換の CPU 秒は import の欄に 1 度だけ入る(#1752)。
        cold_imports = counter.import_tally().since(imports_before)
        for name in ("budget_outer_mod", "budget_inner_mod"):
            sys.modules.pop(name, None)
        importlib.invalidate_caches()
        before = counter.tally()
        importlib.import_module("budget_outer_mod")
        cached = counter.tally().since(before)
    finally:
        counter.uninstall()
        for name in ("budget_outer_mod", "budget_inner_mod"):
            sys.modules.pop(name, None)
    assert cold.count == 2
    assert cold.cpu_seconds == 0.0
    assert cold_imports.cpu_seconds > 0
    assert cached == CompileTally(0, 0.0)


def test_judge_uses_budget_and_registry() -> None:
    assert isinstance(judge(_measurement(0.5), 1.0, {}), WithinBudget)
    assert isinstance(judge(_measurement(1.5), 1.0, {}), OverBudget)
    assert isinstance(judge(_measurement(1.5), 1.0, {"t.hy::test": "理由"}), RegisteredOverBudget)


def test_registry_file_must_be_named_by_key_hash_and_carry_a_reason(tmp_path) -> None:
    key = "t.hy::test"
    (tmp_path / registry_file_name(key)).write_text(f"{key}\n遅い理由\n", encoding="utf-8")
    assert load_registry(tmp_path) == {key: "遅い理由"}
    (tmp_path / "000000000000.txt").write_text("other\n理由\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="hash"):
        load_registry(tmp_path)
    (tmp_path / "000000000000.txt").unlink()
    (tmp_path / registry_file_name("empty")).write_text("empty\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="理由"):
        load_registry(tmp_path)


def test_without_settings_nothing_is_measured(pytester: pytest.Pytester) -> None:
    _project(pytester, "", {"test_slow": SLOW_CALL})
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2)
    assert "doeff の検の時間の上限" not in result.stdout.str()


def test_report_mode_warns_and_lists_but_stays_green(pytester: pytest.Pytester) -> None:
    """報告のみの形(既定)は、超えた検を警告と一覧に出し、赤にしない。"""
    _project(pytester, "doeff_test_call_budget_seconds = 0.05\n", {"test_slow": SLOW_CALL})
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(
        [
            "*BudgetWarning: doeff の検の時間の上限を超えた: test_slow.hy::test_slow の実行(call)*",
            "*上限を超えた(報告のみ*test_slow.hy::test_slow(call CPU *",
        ]
    )
    assert "test_fast(call" not in result.stdout.str()


# 超過を赤にする判定の検は、上限の基準の build(検査なし)に固定する — 日次は make sync の検査つきの build で走り、
# 8d4ff1bb からそこでは超過を赤にしない(日次 t104 の赤・agora-redesign #857)。子の process で走る検は、plugin が
# build の種類を読む pytest_configure より前に読まれる conftest で固定する。
PIN_UNCHECKED_VM_BUILD = "\nimport doeff_vm\n\ndoeff_vm.invariant_checks_enabled = lambda: False\n"


def test_fail_mode_fails_the_slow_test_with_time_and_budget(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    import doeff_vm

    monkeypatch.setattr(doeff_vm, "invariant_checks_enabled", lambda: False)
    _project(
        pytester,
        'doeff_test_call_budget_seconds = 0.05\ndoeff_test_budget_mode = "fail"\n',
        {"test_slow": SLOW_CALL},
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*test_slow.hy::test_slow の実行(call)が CPU *上限 CPU 0.050 秒*"])


FAIL_MODE_INI = 'doeff_test_call_budget_seconds = 0.05\ndoeff_test_budget_mode = "fail"\n'


def test_checked_vm_build_reports_but_does_not_fail(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """doeff-vm が検査つきの build の時は、fail の形でも超過を赤にせず、見出しにその旨を出す(上限は検査なしが基準)。"""
    import doeff_vm

    monkeypatch.setattr(doeff_vm, "invariant_checks_enabled", lambda: True)
    _project(pytester, FAIL_MODE_INI, {"test_slow": SLOW_CALL})
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(
        [
            "*doeff-vm は検査つきの build(invariant-checks)— 上限は検査なしの build が基準のため、この走行の超過は判定しない*",
            "*上限を超えた(報告のみ*test_slow.hy::test_slow(call CPU *",
        ]
    )


def test_unchecked_vm_build_fails_and_names_the_build(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    import doeff_vm

    monkeypatch.setattr(doeff_vm, "invariant_checks_enabled", lambda: False)
    _project(pytester, FAIL_MODE_INI, {"test_slow": SLOW_CALL})
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(
        ["*doeff-vm は検査なしの build — 上限の基準の build で判定する*", "*上限を超えた(赤*"]
    )


def test_unknown_vm_build_is_named_and_judged_as_configured(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """build の種類を読めない(古い VM で関数が無い)時は「不明」と出し、判定は設定のまま。"""
    import doeff_vm

    monkeypatch.delattr(doeff_vm, "invariant_checks_enabled")
    _project(pytester, FAIL_MODE_INI, {"test_slow": SLOW_CALL})
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*doeff-vm の build の種類は不明(*invariant_checks_enabled が無い*"])


def _registered_project(pytester: pytest.Pytester, mode: str) -> None:
    """遅い検 test_slow と速い検 test_fast の両方を登録簿に載せた project(上限 0.05 秒)。"""
    _project(
        pytester,
        f'doeff_test_call_budget_seconds = 0.05\ndoeff_test_budget_mode = "{mode}"\n'
        'doeff_test_budget_registry = "budget-breaches"\n',
        {"test_slow": SLOW_CALL},
    )
    pytester.makeconftest(CONFTEST + PIN_UNCHECKED_VM_BUILD)
    registry = pytester.mkdir("budget-breaches")
    for key in ("test_slow.hy::test_slow", "test_slow.hy::test_fast"):
        (registry / registry_file_name(key)).write_text(f"{key}\n既存の遅い検\n", encoding="utf-8")


def test_registered_test_is_not_failed_and_stale_entries_are_reported(
    pytester: pytest.Pytester,
) -> None:
    """report の形: 登録簿に載った超過は赤にしない。上限の内に戻った登録は「消せる」と出すだけ。"""
    _registered_project(pytester, "report")
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(
        [
            "*登録簿に載った超過: test_slow.hy::test_slow(call*",
            f"*上限の内に戻った登録: test_slow.hy::test_fast — 登録簿の file {registry_file_name('test_slow.hy::test_fast')} を消せる*",
        ]
    )


def test_stale_registration_is_red_in_fail_mode(pytester: pytest.Pytester) -> None:
    """fail の形: 登録簿に載った検が上限の半分以下で終われば、古い登録として赤(消す file を名指す)。載った超過は赤にしない。
    反例 = 登録を消せば両方とも緑(遅い検は載ったまま)。"""
    _registered_project(pytester, "fail")
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(
        [f"*test_slow.hy::test_fast は登録簿に載っているが*古い登録なので登録簿の file {registry_file_name('test_slow.hy::test_fast')} を消す*"]
    )
    (pytester.path / "budget-breaches" / registry_file_name("test_slow.hy::test_fast")).unlink()
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2)


def test_registered_test_near_the_budget_is_reported_not_red() -> None:
    """上限の半分から上限までの間で終わった載った検は消せると報告するだけ(揺れで合否を行き来しない)。半分以下は古い登録。"""
    registry = {"t.hy::test": "理由"}
    near = judge(_measurement(0.8), 1.0, registry)
    assert isinstance(near, RegisteredWithinBudget) and not near.stale
    far = judge(_measurement(0.3), 1.0, registry)
    assert isinstance(far, RegisteredWithinBudget) and far.stale
    assert isinstance(judge(_measurement(0.3), 1.0, {}), WithinBudget)


def test_collect_budget_subtracts_compilation_but_fails_slow_import(
    pytester: pytest.Pytester,
) -> None:
    """キャッシュ無し(この repo の検は PYTHONDONTWRITEBYTECODE=1 で撃つ)で、変換の中で重い file(macro の展開が遅い)は
    赤にせず、import の実行が重い file は赤にする。"""
    _project(
        pytester,
        'doeff_test_collect_budget_seconds = 0.05\ndoeff_test_budget_mode = "fail"\n',
        {"test_slow_collect": SLOW_COLLECT, "test_slow_compile": SLOW_COMPILE},
    )
    pytester.makeconftest(CONFTEST + PIN_UNCHECKED_VM_BUILD)
    result = pytester.runpytest_subprocess(
        "-q", "-p", "no:cacheprovider", "--continue-on-collection-errors"
    )
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(
        [
            "*test_slow_collect.hy の読み込み(import)が CPU *キャッシュ無しの変換 * 回の CPU * 秒を引いた*"
        ]
    )
    assert "test_slow_compile.hy の読み込み(import)" not in result.stdout.str()


# 重い依存(本体の実行で CPU を回す Python の module)を import するだけの軽い検の file — 2 本が同じ依存を読む。
HEAVY_DEPENDENCY = (
    "import time\n_t0 = time.process_time()\nwhile time.process_time() - _t0 < 0.3:\n    pass\nVALUE = 1\n"
)
READS_HEAVY_DEPENDENCY = """\
(require doeff-hy.macros [deftest])
(import budget-heavy-dependency [VALUE])
(deftest test-reads
  (assert (= VALUE 1)))
"""


def test_collect_budget_does_not_charge_a_shared_dependency_to_the_first_file(
    pytester: pytest.Pytester,
) -> None:
    """共有の依存の一度きりの import の重さは、どの file の import にも乗せない — 並び順・1 file だけの実行で合否を変えない
    (agora-redesign #1752)。file 自身の本体が重い file は今までどおり赤(上の test_collect_budget_subtracts_…)。"""
    _project(
        pytester,
        'doeff_test_collect_budget_seconds = 0.05\ndoeff_test_budget_mode = "fail"\n',
        {"test_reads_a": READS_HEAVY_DEPENDENCY, "test_reads_b": READS_HEAVY_DEPENDENCY},
    )
    pytester.makeconftest(CONFTEST + PIN_UNCHECKED_VM_BUILD)
    pytester.makepyfile(budget_heavy_dependency=HEAVY_DEPENDENCY)
    for args in (("test_reads_a.hy",), ("test_reads_b.hy",), ("test_reads_b.hy", "test_reads_a.hy")):
        result = pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider", *args)
        result.assert_outcomes(passed=len(args))
        assert "の読み込み(import)が" not in result.stdout.str(), args


def test_compile_counter_sets_aside_a_first_import_once_and_not_a_loaded_module(tmp_path, monkeypatch) -> None:
    """別の module の初回の import は import の欄に 1 度だけ入り(中の変換は二重に足さない)、sys.modules に在る module の
    import は数えない。"""
    import importlib
    import sys

    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "budget_first_import.py").write_text(HEAVY_DEPENDENCY, encoding="utf-8")
    bootstrap = sys.modules["importlib._bootstrap"]
    original_find_and_load = bootstrap._find_and_load
    counter = CompileCounter()
    counter.install()
    try:
        compile_before = counter.tally()
        imports_before = counter.import_tally()
        importlib.import_module("budget_first_import")
        first_compile = counter.tally().since(compile_before)
        first_imports = counter.import_tally().since(imports_before)
        imports_before = counter.import_tally()
        importlib.import_module("budget_first_import")
        again = counter.import_tally().since(imports_before)
    finally:
        counter.uninstall()
        sys.modules.pop("budget_first_import", None)
    assert first_imports.count == 1
    assert first_imports.cpu_seconds >= 0.3
    assert first_compile.count == 1 and first_compile.cpu_seconds == 0.0
    assert again == ImportTally(0, 0.0)
    assert bootstrap._find_and_load is original_find_and_load


def test_import_budget_is_judged_at_setup_when_collected_from_records(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """記録から収集した file(2 回目 — 1 回目の import が pyc と記録を書く)は、module の import が item の setup で起き、
    上限はそこで同じ鍵で判定される(赤はその item の setup の誤り・agora-redesign #1225)。"""
    _project(
        pytester,
        'doeff_test_collect_budget_seconds = 0.05\ndoeff_test_budget_mode = "fail"\n'
        + f'doeff_adr_items_cache = "{tmp_path / "items"}"\n',
        {"test_slow_collect": SLOW_COLLECT},
    )
    pytester.makeconftest(CONFTEST + PIN_UNCHECKED_VM_BUILD)
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pyc"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "")
    first = pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider", "--continue-on-collection-errors")
    first.assert_outcomes(errors=1)
    second = pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider")
    second.stdout.fnmatch_lines(["*記録から収集 1 file*"])
    second.assert_outcomes(errors=1)
    second.stdout.fnmatch_lines(["*ERROR at setup of test_fast*", "*test_slow_collect.hy の読み込み(import)が CPU *"])
    collected = pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider", "--collect-only")
    collected.stdout.fnmatch_lines(["*1 test collected*"])
    assert "の読み込み(import)が" not in collected.stdout.str()


# 印ごとの上限(agora-redesign #1296)。手元の検 1 秒・real_world の検 10 秒の設定で、測った値を判定の純粋な部分へ渡す
# (実時計で 11 秒待たない)。
LOCAL_AND_EDGE = {"real_world": 10.0}


def _judge_with_markers(
    markers: list[str], cpu: float, registry: dict[str, str] | None = None
) -> Verdict:
    budget = call_budget_for(markers, LOCAL_AND_EDGE, 1.0)
    assert budget is not None
    return judge(_measurement(cpu), budget, registry or {})


def _fail_mode_budgets(registry: dict[str, str]) -> Budgets:
    return Budgets(
        call_seconds=1.0,
        call_seconds_by_marker=LOCAL_AND_EDGE,
        collect_seconds=None,
        mode="fail",
        registry=registry,
        registry_dirs=("scripts/test_budget/OVER-BUDGET", "scripts/doeff_lint/TEST-KIND-BREACHES"),
        vm_build=UncheckedVmBuild(),
    )


def test_unmarked_test_over_one_second_is_red_in_fail_mode() -> None:
    """印の無い検は既定の 1 秒で判定し、1 秒を超えれば fail の形で赤。"""
    assert _fail_mode_budgets({}).fails_over_budget
    verdict = _judge_with_markers([], 1.2)
    assert verdict == OverBudget(_measurement(1.2), 1.0)


def test_real_world_test_of_five_seconds_is_green() -> None:
    assert isinstance(_judge_with_markers(["real_world"], 5.0), WithinBudget)


def test_real_world_test_of_eleven_seconds_is_red() -> None:
    assert _judge_with_markers(["real_world"], 11.0) == OverBudget(_measurement(11.0), 10.0)


def test_budget_is_the_longest_of_the_matching_markers() -> None:
    """複数の印が当たる時は最も長い秒。当たらない印(parametrize など)は選びに効かない。"""
    table = {"real_world": 10.0, "e2e": 30.0}
    assert call_budget_for(["parametrize", "real_world", "e2e"], table, 1.0) == 30.0
    assert call_budget_for(["parametrize"], table, 1.0) == 1.0
    assert call_budget_for(["real_world"], table, None) == 10.0
    assert call_budget_for([], table, None) is None


def test_test_listed_only_in_the_second_registry_is_reported_not_red(tmp_path) -> None:
    """2 つ目の登録簿の dir に載った検は、上限を超えても報告だけ(赤にしない)。"""
    first = tmp_path / "scripts/test_budget/OVER-BUDGET"
    second = tmp_path / "scripts/doeff_lint/TEST-KIND-BREACHES"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    key = "t.hy::test"
    (second / registry_file_name(key)).write_text(f"{key}\n縁の検の既知の超過\n", encoding="utf-8")
    registry = load_registries(tmp_path, _fail_mode_budgets({}).registry_dirs)
    assert registry == {key: "縁の検の既知の超過"}
    assert isinstance(_judge_with_markers([], 1.2, registry), RegisteredOverBudget)


def test_marker_budget_lines_are_parsed_and_errors_are_values() -> None:
    assert parse_marker_budgets(["real_world=10", " e2e = 30 "]) == {"real_world": 10.0, "e2e": 30.0}
    assert parse_marker_budgets([]) == {}
    assert isinstance(parse_marker_budgets(["real_world=ten"]), SettingError)
    assert isinstance(parse_marker_budgets(["real_world"]), SettingError)


MARKED_SLOW_CALL = f"""\
(require doeff-hy.macros [deftest])
(import pytest)
(setv pytestmark [pytest.mark.real_world])
{SPIN}
(deftest test-slow-edge
  (spin 0.3)
  (assert True))
"""

MARKER_INI = (
    'markers = ["real_world: 縁の検"]\n'
    'doeff_test_call_budget_seconds = 0.05\ndoeff_test_budget_mode = "fail"\n'
)


def test_marker_budget_is_chosen_from_the_item_markers(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """印 real_world の検は印の上限で、印の無い検は既定の上限で判定される(pytest の走行の中で item の印を読む)。"""
    import doeff_vm

    monkeypatch.setattr(doeff_vm, "invariant_checks_enabled", lambda: False)
    _project(
        pytester,
        MARKER_INI + 'doeff_test_call_budget_by_marker = ["real_world=5"]\n',
        {"test_slow": SLOW_CALL, "test_edge": MARKED_SLOW_CALL},
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2, failed=1)
    result.stdout.fnmatch_lines(["*test_slow.hy::test_slow の実行(call)が CPU *上限 CPU 0.050 秒*"])
    assert "test_edge.hy::test_slow_edge の実行" not in result.stdout.str()


def test_marker_budget_alone_turns_the_plugin_on_and_fails_the_marked_test(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """印ごとの上限だけの設定でも測る。印の上限を超えた印つきの検は赤、印の無い検は測らない。"""
    import doeff_vm

    monkeypatch.setattr(doeff_vm, "invariant_checks_enabled", lambda: False)
    _project(
        pytester,
        'markers = ["real_world: 縁の検"]\ndoeff_test_budget_mode = "fail"\n'
        'doeff_test_call_budget_by_marker = ["real_world=0.05"]\n',
        {"test_slow": SLOW_CALL, "test_edge": MARKED_SLOW_CALL},
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2, failed=1)
    result.stdout.fnmatch_lines(["*test_edge.hy::test_slow_edge の実行(call)が CPU *上限 CPU 0.050 秒*"])


def test_second_registry_dir_is_read_in_a_run(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """登録簿の dir を list で 2 つ書くと、2 つ目に載った検も報告だけになる。"""
    import doeff_vm

    monkeypatch.setattr(doeff_vm, "invariant_checks_enabled", lambda: False)
    _project(
        pytester,
        MARKER_INI + 'doeff_test_budget_registry = ["over-budget", "kind-breaches"]\n',
        {"test_slow": SLOW_CALL},
    )
    pytester.mkdir("over-budget")
    second = pytester.mkdir("kind-breaches")
    key = "test_slow.hy::test_slow"
    (second / registry_file_name(key)).write_text(f"{key}\n既存の遅い検\n", encoding="utf-8")
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["*登録簿に載った超過: test_slow.hy::test_slow(call*"])


def test_python_test_files_are_not_measured(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(CONFTEST)
    pytester.makepyprojecttoml(
        '[tool.pytest.ini_options]\ndoeff_test_call_budget_seconds = 0.05\ndoeff_test_budget_mode = "fail"\n'
    )
    pytester.makepyfile(
        test_py="import time\n\ndef test_spin():\n    t0 = time.process_time()\n"
        "    while time.process_time() - t0 < 0.3:\n        pass\n"
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize(
    ("ini", "message"),
    [
        ('doeff_test_call_budget_seconds = "abc"\n', "正の秒の数"),
        ("doeff_test_call_budget_seconds = 0\n", "正の秒の数"),
        ('doeff_test_call_budget_seconds = 1\ndoeff_test_budget_mode = "loud"\n', "report か fail"),
        (
            'doeff_test_call_budget_by_marker = ["real_world=abc"]\n',
            "doeff_test_call_budget_by_marker の real_world は正の秒の数: 'abc'",
        ),
        ('doeff_test_call_budget_by_marker = ["real_world=0"]\n', "real_world は正の秒の数"),
        ('doeff_test_call_budget_by_marker = ["real_world"]\n', "印=秒 の形: 'real_world'"),
        ('doeff_test_call_budget_by_marker = ["=10"]\n', "印=秒 の形: '=10'"),
        (
            'doeff_test_call_budget_by_marker = ["real_world=10", "real_world=5"]\n',
            "印 real_world の行が 2 つある",
        ),
    ],
)
def test_unreadable_settings_stop_the_session(
    pytester: pytest.Pytester, ini: str, message: str
) -> None:
    _project(pytester, ini, {"test_slow": SLOW_CALL})
    result = pytester.runpytest("-q")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines([f"*{message}*"])


def test_the_analyzers_uncached_expansion_is_subtracted_like_bytecode_compilation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """解析器の Hy の展開(構文木の cache に当たらない時だけ)も数えに乗り、install の外では乗らない(agora-redesign #1534)。"""
    program_effects = pytest.importorskip("doeff_effect_analyzer.program_effects")
    monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", str(tmp_path / "trees"))
    source = "(setv answer (+ 1 2))\n"
    counter = CompileCounter()
    counter.install()
    try:
        before = counter.tally()
        program_effects._compile_hy(source, "/src/budget_expanded.hy", "budget_expanded")
        cold = counter.tally().since(before)
        before = counter.tally()
        program_effects._compile_hy(source, "/src/budget_expanded.hy", "budget_expanded")
        cached = counter.tally().since(before)
    finally:
        counter.uninstall()
    before = counter.tally()
    program_effects._compile_hy(source.replace("2", "3"), "/src/budget_expanded.hy", "budget_expanded")
    outside = counter.tally().since(before)
    assert cold.count == 1 and cold.cpu_seconds > 0
    assert cached.count == 0
    assert outside.count == 0
