# moved-submodule-shim: doeff_core_effects.proc_table_effects -> doeff_core_effects._proc_table_effects
# moved-submodule-shim-names doeff_core_effects.proc_table_effects:
#   ENDED_STATES ProcRow ProcessSignal ReadProcTable STAT_PPID STAT_RSS_PAGES STAT_START_TICKS
#   SignalPid proc_row_of_stat
"""Moved:``doeff_core_effects.proc_table_effects`` is now ``doeff_core_effects._proc_table_effects`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.proc_table_effects…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._proc_table_effects import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._proc_table_effects", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._proc_table_effects")
