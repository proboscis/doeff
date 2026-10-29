# moved-submodule-shim: doeff_system_one.types -> doeff_system_one._types
# moved-submodule-shim-names doeff_system_one.types:
#   Answer IMAGE_FORMATS Image JudgeError JudgeUsageLimited KINDS Kind Question Verdict
#   answer_choice answer_noul answer_score choice noul score
"""Moved: ``doeff_system_one.types`` is now ``doeff_system_one._types`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_system_one.types…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_system_one._types import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_system_one._types", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_system_one._types")
