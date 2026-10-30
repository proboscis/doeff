# moved-submodule-shim: doeff_core_effects.process_exit -> doeff_core_effects._process_exit
# moved-submodule-shim-names doeff_core_effects.process_exit:
#   Await AwaitProcessExit AwaitWarmChildExit ENDED_STATES EXIT_FD_OPENERS ExitEnd
#   ExitWaitUnavailable MODULE_TAGS ProcStat ProcessEnded ProcessNotChild STARTED_CHILDREN
#   child_running child_unreaped ended exit_fd_of exit_fd_opener_for kqueue_fd_of pidfd_of
#   proc_stat_of process_exit_handler same_child_running
"""Moved:``doeff_core_effects.process_exit`` is now ``doeff_core_effects._process_exit`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.process_exit…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._process_exit import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._process_exit", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._process_exit")
