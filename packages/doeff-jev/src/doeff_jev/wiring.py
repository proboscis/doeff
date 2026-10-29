# moved-submodule-shim: doeff_jev.wiring -> doeff_jev._wiring
# moved-submodule-shim-names doeff_jev.wiring:
#   DEFAULT_CACHE_DB DEFAULT_JOURNAL JevTarget durable_cache jev_handler jev_memo_handler
#   journal_handler judge_stack target_from_process_environment
"""Moved: ``doeff_jev.wiring`` is now ``doeff_jev._wiring`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_jev.wiring…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_jev._wiring import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_jev._wiring", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_jev._wiring")
