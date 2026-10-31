"""Effects that read the doeff-vm's counts and set its switches.

The counts and switches belong to the VM of the whole process; they were functions of
``doeff_vm`` (``vm_live_counts``, ``vm_work_counts``, ...) and are effects now, so a user
sees doeff's effects only. ``vm_probe_handler``
(``doeff_core_effects._handlers``) answers every one of them by asking the VM:

    from doeff import ReadVmWorkCounts, run
    from doeff_core_effects._handlers import vm_probe_handler

    steps, handler_calls = run(vm_probe_handler(ReadVmWorkCounts()))

The counts are running totals of the process (they never go down), so the difference of
two reads is the work done between them; a read inside a run includes the steps the run
took to reach it.
"""

from doeff import EffectBase


class ReadVmLiveCounts(EffectBase[tuple[int, int, int]]):
    """Read the VM objects alive in this process: (segments, continuations, IR streams).

    Once a run has finished, the counts are back to what they were before it.
    """

    def __init__(self) -> None:
        super().__init__()

    def __repr__(self) -> str:
        return "ReadVmLiveCounts()"


class ReadVmWorkCounts(EffectBase[tuple[int, int]]):
    """Read the work every VM of this process has done so far: (steps, handler calls).

    Running totals that a busy machine does not shift: the same program adds the same
    numbers each time it runs.
    """

    def __init__(self) -> None:
        super().__init__()

    def __repr__(self) -> str:
        return "ReadVmWorkCounts()"


class ReadVmScanCounts(EffectBase[dict[str, int]]):
    """Read the entries the Rust scheduler's scans have looked at so far, by what they walked.

    Keys: ``ready``, ``waiters``, ``semaphore_waiters``, ``sweep`` (the scheduler's
    structures) and ``chain_fibers`` (fibers looked at in a detached continuation chain).
    Running totals, like ``ReadVmWorkCounts``.
    """

    def __init__(self) -> None:
        super().__init__()

    def __repr__(self) -> str:
        return "ReadVmScanCounts()"


class ReadInvariantChecks(EffectBase[bool]):
    """Read whether the VM checks its invariants at every step in this process.

    Every build carries the checks; they run only when turned on
    (``DOEFF_VM_INVARIANT_CHECKS=1`` or ``SetInvariantChecks(True)``). A run with them on
    is many times slower.
    """

    def __init__(self) -> None:
        super().__init__()

    def __repr__(self) -> str:
        return "ReadInvariantChecks()"


class SetInvariantChecks(EffectBase[None]):
    """Turn the VM's per-step invariant checks on or off for this process."""

    def __init__(self, enabled: bool) -> None:
        super().__init__()
        self.enabled = enabled

    def __repr__(self) -> str:
        return f"SetInvariantChecks({self.enabled!r})"


class SetSchedulerStepBudgetArmed(EffectBase[None]):
    """Switch the flag the Rust scheduler reads at every wake: whether a step budget is armed.

    ``doeff_core_effects._scheduler.arm_step_budget`` and ``disarm_step_budget`` switch it
    together with the budget they arm; with the flag on and no budget armed, a wake checks
    nothing.
    """

    def __init__(self, armed: bool) -> None:
        super().__init__()
        self.armed = armed

    def __repr__(self) -> str:
        return f"SetSchedulerStepBudgetArmed({self.armed!r})"


class SetSchedulerTraceInstalled(EffectBase[None]):
    """Switch the flag the Rust scheduler reads at every event: whether a trace sink is installed.

    ``doeff_core_effects._scheduler.set_scheduler_trace`` switches it together with the sink
    it installs; with the flag on and no sink installed, an event reaches no one.
    """

    def __init__(self, installed: bool) -> None:
        super().__init__()
        self.installed = installed

    def __repr__(self) -> str:
        return f"SetSchedulerTraceInstalled({self.installed!r})"
