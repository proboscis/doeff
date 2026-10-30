# moved-submodule-shim: doeff_core_effects.scripted_warm_process -> doeff_core_effects._scripted_warm_process
# moved-submodule-shim-names doeff_core_effects.scripted_warm_process:
#   ForkFromWarm MODULE_TAGS PollWarmChild ProcessSignal SCRIPTED_WARM_FIRST_PID
#   SCRIPTED_WARM_FIRST_TICKS SignalWarmChild WarmChildState WarmExited WarmForked WarmGone
#   WarmInputWritten WarmLost WarmRefused WarmRun WarmRunning WarmScript WarmSignaled WarmSocket
#   WarmSocketAnswer WriteWarmChildInput ended_answer scripted_fork scripted_warm_process_handler
#   signal_number_of warm_answer_late warm_gone warm_lost warm_socket_missing
"""Moved:``doeff_core_effects.scripted_warm_process`` is now ``doeff_core_effects._scripted_warm_process`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.scripted_warm_process…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._scripted_warm_process import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._scripted_warm_process", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._scripted_warm_process")
