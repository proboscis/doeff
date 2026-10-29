# moved-submodule-shim: doeff_hy.check_slot -> doeff_hy._check_slot
# moved-submodule-shim-names doeff_hy.check_slot:
#   CANON_CALLS CANON_ERRORS CANON_OPTIONAL_CALLS CANON_UNDER_HOME EnvironmentByName MARK
#   MODULE_KEY_PREFIX SlotExpired SlotHeld absent_optional canon_module slot_label take_slot
#   unfit_names
"""Moved: ``doeff_hy.check_slot`` is now ``doeff_hy._check_slot`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.check_slot…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy._check_slot import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._check_slot", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._check_slot")
