# moved-submodule-shim: doeff_core_effects.handlers -> doeff_core_effects._handlers
# moved-submodule-shim-names doeff_core_effects.handlers:
#   Ask Await Get Listen Local MissingEnvKeyError Put ReadInvariantChecks ReadVmLiveCounts
#   ReadVmScanCounts ReadVmWorkCounts ReadWriterLog SetInvariantChecks SetSchedulerStepBudgetArmed
#   SetSchedulerTraceInstalled Slog Try WriterTellEffect await_handler env_var_ask lazy_ask
#   listen_handler local_handler reader slog_discard_handler slog_handler state try_handler
#   vm_probe_handler writer
"""Moved: ``doeff_core_effects.handlers`` is now ``doeff_core_effects._handlers`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.handlers…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._handlers import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._handlers", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._handlers")
