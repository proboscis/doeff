# moved-submodule-shim: doeff_core_effects.scripted_process -> doeff_core_effects._scripted_process
# moved-submodule-shim-names doeff_core_effects.scripted_process:
#   AppendText EnvEntry EnvMode ExecutableAt FileFailed InterpreterFacts MODULE_TAGS MakeDirectory
#   ModuleFound ModuleNotFound PathKind PathStat PollProcess ProcessAlive ProcessExited
#   ProcessInputWritten ProcessNotChild ProcessNotStarted ProcessOutcome ProcessRunning
#   ProcessScript ProcessSignal ProcessSignalled ProcessStarted ReadEnvironment ReadInterpreter
#   ReadMachineName ResolveModule RunProcess SCRIPTED_COMMAND_EFFECTS SCRIPTED_FIRST_PID
#   SCRIPTED_RUNNING SCRIPTED_STOPPED_CODE ScriptedCommand SignalProcess StartProcess StatPath
#   StopProcess WorkingDirectory WriteProcessInput environment_answer executable_file_answer
#   not_started_outcome run_scripted scripted_answer scripted_append_outputs scripted_child_env
#   scripted_command_peer scripted_executable_at scripted_open_outputs scripted_process_handler
#   start_refusal
"""Moved:``doeff_core_effects.scripted_process`` is now ``doeff_core_effects._scripted_process`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.scripted_process…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._scripted_process import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._scripted_process", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._scripted_process")
