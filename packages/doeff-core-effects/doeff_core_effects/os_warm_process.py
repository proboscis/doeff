# moved-submodule-shim: doeff_core_effects.os_warm_process -> doeff_core_effects._os_warm_process
# moved-submodule-shim-names doeff_core_effects.os_warm_process:
#   ANSWER_LIMIT ENDED_STATES ForkFromWarm HeldInput MODULE_TAGS PROC_ROOT PollWarmChild ProcStat
#   ProcessSignal SignalWarmChild WARM_ANSWER_SECONDS WarmEnvWire WarmExited WarmForked
#   WarmForkedWire WarmGone WarmInputWritten WarmLost WarmProcUnavailable WarmRefused
#   WarmRefusedWire WarmRequestWire WarmRunning WarmSignaled WriteWarmChildInput darwin_proc_stat
#   darwin_thread_count input_written linux_proc_stat os_fork_from_warm os_poll_warm_child
#   os_signal_warm_child os_warm_process_handler own_thread_count proc_stat_of received_line
#   same_child_running signal_number_of warm_answer_late warm_answer_line warm_answer_of
#   warm_answer_unreadable warm_exit_answer warm_gone warm_lost warm_request_line warm_request_of
#   warm_socket_missing warm_socket_refused wire_line
"""Moved:``doeff_core_effects.os_warm_process`` is now ``doeff_core_effects._os_warm_process`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.os_warm_process…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._os_warm_process import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._os_warm_process", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._os_warm_process")
