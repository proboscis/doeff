# moved-submodule-shim: doeff_core_effects.stop_signal_handlers -> doeff_core_effects._stop_signal_handlers
# moved-submodule-shim-names doeff_core_effects.stop_signal_handlers:
#   AwaitStop CompletePromise CreateExternalPromise CreatePromise ExternalPromise MODULE_TAGS
#   RaiseStop STOP_SIGNALS StopBell StopBox StopRequested Wait install_stop_box
#   os_signal_stop_handler scripted_stop_handler
"""Moved:``doeff_core_effects.stop_signal_handlers`` is now ``doeff_core_effects._stop_signal_handlers`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.stop_signal_handlers…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._stop_signal_handlers import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._stop_signal_handlers", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._stop_signal_handlers")
