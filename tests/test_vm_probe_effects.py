"""doeff-vm の数と切り替えは doeff の effect で読み書きし、doeff-vm の関数としては見えない事。

決まり: doeff の subpackage が利用者に見せる公開 API は effect と handler だけ。doeff-vm の数を測る・設定する関数 9 個
(doeff_vm の根の vm_live_counts・invariant_checks_enabled と、拡張 doeff_vm.doeff_vm の 7 個)は、effect
(ReadVmLiveCounts・ReadVmWorkCounts・ReadVmScanCounts・ReadInvariantChecks・SetInvariantChecks・SetSchedulerStepBudgetArmed・
SetSchedulerTraceInstalled)にして doeff の根から出し、doeff_core_effects._handlers.vm_probe_handler が VM に問うて答える。
拡張の関数は _ で始まる名で拡張の __all__ の外に置く(src/lib.rs の bind_internal)。

失敗ケース: 古い名が doeff_vm か拡張の公開の名に戻る・_ の名が拡張の __all__ に載る・effect が拡張の関数と違う値を答える・
handler が別の関数を呼ぶと赤。
"""

from __future__ import annotations

import doeff_vm
import pytest
from doeff_core_effects import reader, vm_probe_handler
from doeff_vm import doeff_vm as vm_ext

import doeff
from doeff import (
    Ask,
    EffectBase,
    ReadInvariantChecks,
    ReadVmLiveCounts,
    ReadVmScanCounts,
    ReadVmWorkCounts,
    SetInvariantChecks,
    SetSchedulerStepBudgetArmed,
    SetSchedulerTraceInstalled,
    do,
    run,
)

#: 拡張の関数の今の名(_ で始まる)と、前の公開の名。
INTERNAL_FUNCTIONS = (
    "_vm_live_counts",
    "_vm_work_counts",
    "_vm_scan_counts",
    "_invariant_checks_enabled",
    "_set_invariant_checks",
    "_set_scheduler_step_budget_armed",
    "_set_scheduler_trace_installed",
)
OLD_PUBLIC_NAMES = tuple(name.removeprefix("_") for name in INTERNAL_FUNCTIONS)
EFFECTS = (
    ReadVmLiveCounts,
    ReadVmWorkCounts,
    ReadVmScanCounts,
    ReadInvariantChecks,
    SetInvariantChecks,
    SetSchedulerStepBudgetArmed,
    SetSchedulerTraceInstalled,
)


def _asked(effect: EffectBase) -> object:
    """effect を vm_probe_handler の下で 1 回走らせた答え。"""
    return run(vm_probe_handler(effect))


def test_the_old_names_are_not_public_in_doeff_vm() -> None:
    assert [name for name in ("vm_live_counts", "invariant_checks_enabled") if hasattr(doeff_vm, name)] == []
    assert [name for name in OLD_PUBLIC_NAMES if hasattr(vm_ext, name)] == []
    assert [name for name in (*OLD_PUBLIC_NAMES, *INTERNAL_FUNCTIONS) if name in vm_ext.__all__] == []
    assert [name for name in OLD_PUBLIC_NAMES if hasattr(doeff, name)] == []


def test_the_extension_keeps_the_functions_inside() -> None:
    assert [name for name in INTERNAL_FUNCTIONS if not callable(getattr(vm_ext, name))] == []


def test_the_effects_come_from_the_doeff_root() -> None:
    assert [effect.__name__ for effect in EFFECTS if not issubclass(effect, EffectBase)] == []
    assert [effect.__name__ for effect in EFFECTS if getattr(doeff, effect.__name__) is not effect] == []


@pytest.mark.parametrize(
    ("effect", "function", "arguments"),
    [
        (ReadVmLiveCounts(), "_vm_live_counts", ()),
        (ReadVmWorkCounts(), "_vm_work_counts", ()),
        (ReadVmScanCounts(), "_vm_scan_counts", ()),
        (ReadInvariantChecks(), "_invariant_checks_enabled", ()),
        (SetInvariantChecks(False), "_set_invariant_checks", (False,)),
        (SetSchedulerStepBudgetArmed(True), "_set_scheduler_step_budget_armed", (True,)),
        (SetSchedulerTraceInstalled(True), "_set_scheduler_trace_installed", (True,)),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, EffectBase) else None,
)
def test_each_effect_is_answered_by_its_vm_function(
    monkeypatch: pytest.MonkeyPatch, effect: EffectBase, function: str, arguments: tuple[object, ...]
) -> None:
    """handler は effect ごとに拡張の 1 つの関数を引数ごと呼び、その答えを返す(関数は偽物 — VM の状態は変えない)。"""
    calls: tuple[tuple[object, ...], ...] = ()
    answer = object()

    def recorded(*given: object) -> object:
        """拡張の関数の代わりに、呼ばれた引数を残して決まった答えを返すため。"""
        nonlocal calls
        calls = (*calls, given)
        return answer

    monkeypatch.setattr(vm_ext, function, recorded)
    assert _asked(effect) is answer
    assert calls == (arguments,)


def test_the_reads_answer_the_values_the_vm_holds() -> None:
    """effect の読みは、読む run の前後に拡張の関数で直に読んだ値の間に在る(数は積み上げ — 読む run の分だけ増える)。"""
    work_before = vm_ext._vm_work_counts()
    work = _asked(ReadVmWorkCounts())
    work_after = vm_ext._vm_work_counts()
    assert work_before < work <= work_after
    assert all(isinstance(count, int) for count in work)

    live_outside = vm_ext._vm_live_counts()
    live = _asked(ReadVmLiveCounts())
    # 読む run の中の数は、その run の物だけ外より多く、run が終われば外の数へ戻る。
    assert all(inside >= outside for inside, outside in zip(live, live_outside, strict=True))
    assert vm_ext._vm_live_counts() == live_outside

    scans_before = vm_ext._vm_scan_counts()
    scans = _asked(ReadVmScanCounts())
    assert set(scans) == set(scans_before)
    assert all(scans[name] >= scans_before[name] for name in scans)

    assert _asked(ReadInvariantChecks()) is vm_ext._invariant_checks_enabled()


def test_set_invariant_checks_switches_the_vm_and_reads_back() -> None:
    @do
    def switch_off_and_back(before: bool):
        yield SetInvariantChecks(not before)
        switched = yield ReadInvariantChecks()
        yield SetInvariantChecks(before)
        return switched, (yield ReadInvariantChecks())

    before = vm_ext._invariant_checks_enabled()
    try:
        assert run(vm_probe_handler(switch_off_and_back(before))) == (not before, before)
        assert vm_ext._invariant_checks_enabled() is before
    finally:
        vm_ext._set_invariant_checks(before)


def test_other_effects_pass_through_to_the_outer_handler() -> None:
    @do
    def asks_and_reads():
        value = yield Ask("key")
        steps, _ = yield ReadVmWorkCounts()
        return value, steps > 0

    assert run(reader({"key": "value"})(vm_probe_handler(asks_and_reads()))) == ("value", True)
