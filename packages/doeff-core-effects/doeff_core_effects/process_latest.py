# moved-submodule-shim: doeff_core_effects.process_latest -> doeff_core_effects._process_latest
# moved-submodule-shim-names doeff_core_effects.process_latest:
#   AwaitLatest BELLS BELLS_LOCK BOARDS BOARDS_LOCK CreateExternalPromise ExternalPromise
#   MODULE_TAGS PRIORITY_IDLE PublishLatest ReadLatest Wait hang_bell latest_bells latest_board
#   process_latest_handler ring_bells unhang_bell
"""Moved:``doeff_core_effects.process_latest`` is now ``doeff_core_effects._process_latest`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.process_latest…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._process_latest import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._process_latest", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._process_latest")
