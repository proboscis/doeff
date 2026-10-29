# moved-submodule-shim: doeff_core_effects.scheduler -> doeff_core_effects._scheduler
# moved-submodule-shim-names doeff_core_effects.scheduler:
#   AcquireSemaphore Cancel CompletePromise CreateExternalPromise CreatePromise CreateSemaphore
#   DEFAULT_IMPLEMENTATION Discard EXTERNAL_STALL_LOG_INTERVAL_SECONDS ExternalPromise
#   ExternalPromiseCancelCallbackError FailPromise Future Gather HANDLE_REFS_PRUNE_MIN
#   HANDLE_SWEEP_INTERVAL IDLE_SWEEP_ROWS_PER_ID IMPLEMENTATION_ENV_VAR PRIORITY_EXTERNAL_WAIT
#   PRIORITY_HIGH PRIORITY_IDLE PRIORITY_NORMAL ProcessStepBudget Promise
#   ROOT_CLOSE_OUT_GRACE_SECONDS Race ReleaseSemaphore STALL_LOG_WAITERS_SHOWN
#   SchedulerDeadlockError SchedulerImplementation SchedulerIndexDriftError SchedulerIntrospection
#   Semaphore Spawn SpentStepBudget StepBudget StepBudgetExceeded Task TaskCancelledError
#   TaskCompleted TraceSink UNRETRIEVED_EXCEPTIONS_NAMED VmWork Wait arm_step_budget
#   disarm_step_budget resolve_implementation scheduled scheduler_trace_sink set_scheduler_trace
"""Moved: ``doeff_core_effects.scheduler`` is now ``doeff_core_effects._scheduler`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.scheduler…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._scheduler import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._scheduler", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._scheduler")
