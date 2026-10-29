# moved-submodule-shim: doeff_core_effects.os_random -> doeff_core_effects._os_random
# moved-submodule-shim-names doeff_core_effects.os_random:
#   MODULE_TAGS RandomBytes os_random_handler
"""Moved: ``doeff_core_effects.os_random`` is now ``doeff_core_effects._os_random`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.os_random…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._os_random import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._os_random", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._os_random")
