# moved-submodule-shim: doeff_core_effects.os_process -> doeff_core_effects._os_process
# moved-submodule-shim-names doeff_core_effects.os_process:
#   CANCEL_KILLED_METRIC CANCEL_TERMINATED_METRIC CancelStop ChildLifetime ChildPipes ChildWatch
#   CountMetric EnvEntry EnvMode ExecutableAt FileFailed GROUP_POLL InterpreterFacts LIFELINE
#   Lifeline MODULE_TAGS ModuleFound ModuleNotFound PROCESS_THREADS PathKind PathStat PollProcess
#   ProcessAlive ProcessExited ProcessInputWritten ProcessNotChild ProcessNotStarted ProcessOutcome
#   ProcessRunning ProcessSignal ProcessSignalled ProcessStarted ReadEnvironment ReadInterpreter
#   ReadMachineName ResolveModule RunProcess STARTED_CHILDREN SignalProcess StartProcess
#   StartedChildren StopProcess ThreadPerCall WatchedChild WorkingDirectory WriteProcessInput
#   append_output child_environment decoded environment_answer executable_file_answer group_alive
#   group_gone_by keep_nothing metered_offloaded_subprocess_handler not_started_outcome offloaded
#   offloaded_run offloaded_subprocess_handler os_executable_at os_interpreter_facts
#   os_module_location os_process_alive poll_child_process reported_stop run_communicated
#   run_detached run_subprocess run_watched signal_child_process signal_group start_child_process
#   stat_path state stop_cancelled_child stop_child stop_child_process stop_for_cancel
#   stop_on_cancel subprocess_handler timed_out_outcome watched_child write_child_input
"""Moved:``doeff_core_effects.os_process`` is now ``doeff_core_effects._os_process`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.os_process…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._os_process import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._os_process", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._os_process")
