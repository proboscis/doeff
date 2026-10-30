# moved-submodule-shim: doeff_core_effects.darwin_proc -> doeff_core_effects._darwin_proc
# moved-submodule-shim-names doeff_core_effects.darwin_proc:
#   BSD_INFO_SIZE BSD_START_OFFSET BSD_STATUS_OFFSET LIBPROC_PATH MODULE_TAGS PROC_PIDTASKINFO
#   PROC_PIDTBSDINFO ProcStat STATUS_STATES TASK_INFO_SIZE TASK_THREADS_OFFSET bsd_info_stat
#   darwin_proc_stat darwin_thread_count pid_info sized task_info_threads
"""Moved:``doeff_core_effects.darwin_proc`` is now ``doeff_core_effects._darwin_proc`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.darwin_proc…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._darwin_proc import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._darwin_proc", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._darwin_proc")
