# moved-submodule-shim: doeff_core_effects.scheduler_step_tally -> doeff_core_effects._scheduler_step_tally
# moved-submodule-shim-names doeff_core_effects.scheduler_step_tally:
#   CloseStepTally CloseTaskTally EMPTY_STEP_TALLY LOCK MODULE_TAGS OpenStepTally OpenTaskTally
#   ReadTaskTally SiteTally StepTally StepWindow TASK_WINDOWS TaskTally TaskWindow WINDOWS closed
#   empty_task_row note_spawned note_task_step opened scheduler_trace_sink set_scheduler_trace
#   sink_claimed sink_released step_sink step_tally_handler tally_with_sites task_closed task_opened
#   task_read task_table
"""Moved: ``doeff_core_effects.scheduler_step_tally`` is now ``doeff_core_effects._scheduler_step_tally`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.scheduler_step_tally…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._scheduler_step_tally import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._scheduler_step_tally", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._scheduler_step_tally")
