"""The doeff-vm counts and switches are read and written through doeff effects and are not visible as doeff-vm functions.

Rule: the public API that a doeff subpackage shows users is only effects and handlers. The 9 functions that measure or set doeff-vm counts
(vm_live_counts and invariant_checks_enabled at the root of doeff_vm, and the 7 in the extension doeff_vm.doeff_vm) are made into effects
(ReadVmLiveCounts・ReadVmWorkCounts・ReadVmScanCounts・ReadInvariantChecks・SetInvariantChecks・SetSchedulerStepBudgetArmed・
SetSchedulerTraceInstalled) exposed from the doeff root, and doeff_core_effects._handlers.vm_probe_handler asks the VM and answers.
The extension's functions are placed outside the extension's __all__ under names starting with _ (bind_internal in src/lib.rs).

Failure cases: red if an old name returns to the public names of doeff_vm or the extension, if a _ name is listed in the extension's __all__, if an effect answers a value different from the extension's function, or
if the handler calls a different function.
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

#: The extension functions' current names (starting with _) and the earlier public names.
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
    """The answer from running an effect once under vm_probe_handler."""
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
    """For each effect the handler calls one extension function with the arguments and returns its answer (the function is a fake -- it does not change the VM's state)."""
    calls: tuple[tuple[object, ...], ...] = ()
    answer = object()

    def recorded(*given: object) -> object:
        """In place of the extension function, to leave the arguments it was called with and return a fixed answer."""
        nonlocal calls
        calls = (*calls, given)
        return answer

    monkeypatch.setattr(vm_ext, function, recorded)
    assert _asked(effect) is answer
    assert calls == (arguments,)


def test_the_reads_answer_the_values_the_vm_holds() -> None:
    """The effect's reading lies between the values read directly with the extension function before and after the reading run (counts accumulate -- they grow by the amount of the reading run)."""
    work_before = vm_ext._vm_work_counts()
    work = _asked(ReadVmWorkCounts())
    work_after = vm_ext._vm_work_counts()
    assert work_before < work <= work_after
    assert all(isinstance(count, int) for count in work)

    live_outside = vm_ext._vm_live_counts()
    live = _asked(ReadVmLiveCounts())
    # The counts inside the reading run are larger than outside by that run's alone, and return to the outer counts once the run ends.
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
