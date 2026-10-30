# moved-submodule-shim: doeff_core_effects.warm_effects -> doeff_core_effects._warm_effects
# moved-submodule-shim-names doeff_core_effects.warm_effects:
#   AwaitWarmChildExit EXIT_CODE_PATTERN EnvEntry ForkFromWarm PollWarmChild ProcessSignal
#   SignalWarmChild WARM_ANSWER_SECONDS WarmExited WarmForked WarmGone WarmInputWritten WarmLost
#   WarmRefused WarmRunning WarmSignaled WriteWarmChildInput signal_number_of warm_answer_late
#   warm_answer_unreadable warm_exit_answer warm_gone warm_lost warm_socket_missing
#   warm_socket_refused
"""Moved:``doeff_core_effects.warm_effects`` is now ``doeff_core_effects._warm_effects`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.warm_effects…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._warm_effects import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._warm_effects", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._warm_effects")
