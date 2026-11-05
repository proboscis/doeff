# Type declarations for the compiled extension module doeff_vm.doeff_vm (Rust/pyo3 - src/lib.rs): the entries that are not exposed from the package top (doeff_vm/__init__.py).
# Entries that live only in the extension are pulled directly from doeff_vm.doeff_vm by their users, so that an old build's .so does not break the package import
# (see the note on gc_traverse_zeroed_visits in src/lib.rs). Other names in the extension have no type in this declaration (__getattr__) -
# same treatment as before when the extension had no declaration; mirroring every name is out of scope here.
# packages/doeff-vm/tests/test_memory_stats.py checks that the declared functions exist in the extension.
#
# Functions starting with _ are VM counts and switches, not public names (not listed in the extension's __all__ - bind_internal in src/lib.rs).
# Users emit doeff effects (ReadVmWorkCounts, SetInvariantChecks, etc.), and doeff_core_effects._handlers.vm_probe_handler answers them
# by calling these. The only other callers are the scheduler and world_phases in doeff_core_effects.
from _typeshed import Incomplete

def _vm_live_counts() -> tuple[int, int, int]:
    """Number of live objects inside the VM (segments, continuations, IR streams) - returns to the original count once a run ends (effect ReadVmLiveCounts)."""
    ...

def _vm_work_counts() -> tuple[int, int]:
    """Accumulated counts across all VMs in the process (steps, handler invocations) - never decreases. The difference between two reads is the amount of work in between
    (effect ReadVmWorkCounts)。"""
    ...

def _vm_scan_counts() -> dict[str, int]:
    """Table accumulating, per kind of thing seen, the number of entries the Rust scheduler scan visited - never decreases (effect ReadVmScanCounts)."""
    ...

def _invariant_checks_enabled() -> bool:
    """Whether the per-step VM invariant checks are enabled in this process (effect ReadInvariantChecks)."""
    ...

def _set_invariant_checks(on: bool) -> None:
    """Enable or disable the per-step VM invariant checks in this process (effect SetInvariantChecks)."""
    ...

def _set_scheduler_trace_installed(installed: bool) -> None:
    """Toggle the flag of the Rust scheduler's measurement hook (whether a sink is installed) - the only callers are
    doeff_core_effects._scheduler.set_scheduler_trace and the handler of effect SetSchedulerTraceInstalled
    (TRACE_INSTALLED in src/scheduler.rs)."""
    ...

def _set_scheduler_step_budget_armed(armed: bool) -> None:
    """Toggle the flag of the Rust scheduler's step-budget limit (whether a limit is set) - the only callers are
    doeff_core_effects._scheduler.arm_step_budget, disarm_step_budget, and the handler of effect SetSchedulerStepBudgetArmed
    (STEP_BUDGET_ARMED in src/scheduler.rs)."""
    ...

class StateHandler:
    """Responder for state (a Rust handler - src/state_handler.rs). Answers Get / Put (instances of two classes) from the store inside the VM.
    The only constructor is doeff_core_effects._handlers.state. Called by the VM; calling it from Python raises TypeError."""

    def __init__(self, store: dict[object, object], get_type: type, put_type: type, /) -> None: ...
    @property
    def effect_types(self) -> tuple[type, type]: ...
    @property
    def __name__(self) -> str: ...
    @property
    def __qualname__(self) -> str: ...  # pyright: ignore[reportIncompatibleVariableOverride] - not the type's __qualname__ but an instance-level entry that answers the handler's name
    def __call__(self, *args: object, **kwargs: object) -> None: ...

def __getattr__(name: str) -> Incomplete: ...
