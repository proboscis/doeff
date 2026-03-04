"""Scheduler-internal re-exports from the Rust VM.

The Rust scheduler handles external completion blocking directly
(see `block_until_external_completion` in `doeff-core-effects/src/scheduler/mod.rs`).
Spec: `specs/vm/SPEC-SCHED-001-cooperative-scheduling.md` (`switch_to_next`, Priority 3-4).
"""

import doeff_vm

_SchedulerTaskCompleted = doeff_vm._SchedulerTaskCompleted


__all__ = ["_SchedulerTaskCompleted"]
