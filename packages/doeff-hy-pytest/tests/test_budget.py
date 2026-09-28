"""Hy の検の時間の上限(doeff_hy_pytest/budget.py)の検。

時間は CPU 時間で測るので、検の file の中は sleep ではなく CPU を回して時間を使う。上限は小さく(0.05 秒)、
超える側は 0.3 秒回して、機体の揺れで合否が入れ替わらない幅を取る。
"""

from __future__ import annotations

import pytest
from doeff_hy_pytest.budget import (
    CompileCounter,
    CompileTally,
    Measurement,
    OverBudget,
    RegisteredOverBudget,
    RegistryError,
    WithinBudget,
    judge,
    load_registry,
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
        importlib.import_module("budget_outer_mod")
        cold = counter.tally().since(before)
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
    assert cold.cpu_seconds > 0
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


def test_fail_mode_fails_the_slow_test_with_time_and_budget(pytester: pytest.Pytester) -> None:
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


def test_registered_test_is_not_failed_and_stale_entries_are_reported(
    pytester: pytest.Pytester,
) -> None:
    """登録簿に載った超過は赤にしない。上限の内に戻った登録は「消せる」と出す。"""
    _project(
        pytester,
        'doeff_test_call_budget_seconds = 0.05\ndoeff_test_budget_mode = "fail"\n'
        'doeff_test_budget_registry = "budget-breaches"\n',
        {"test_slow": SLOW_CALL},
    )
    registry = pytester.mkdir("budget-breaches")
    for key in ("test_slow.hy::test_slow", "test_slow.hy::test_fast"):
        (registry / registry_file_name(key)).write_text(f"{key}\n既存の遅い検\n", encoding="utf-8")
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(
        [
            "*登録簿に載った超過: test_slow.hy::test_slow(call*",
            f"*上限の内に戻った登録: test_slow.hy::test_fast — 登録簿の file {registry_file_name('test_slow.hy::test_fast')} を消せる*",
        ]
    )


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
    result = pytester.runpytest_subprocess(
        "-q", "-p", "no:cacheprovider", "--continue-on-collection-errors"
    )
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(
        [
            "*test_slow_collect.hy の収集(import を含む)が CPU *キャッシュ無しの変換 * 回の CPU * 秒を引いた*"
        ]
    )
    assert "test_slow_compile.hy の収集" not in result.stdout.str()


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
    ],
)
def test_unreadable_settings_stop_the_session(
    pytester: pytest.Pytester, ini: str, message: str
) -> None:
    _project(pytester, ini, {"test_slow": SLOW_CALL})
    result = pytester.runpytest("-q")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines([f"*{message}*"])
