# moved-submodule-shim: doeff_system_one.programs -> doeff_system_one._programs
# moved-submodule-shim-names doeff_system_one.programs:
#   Judge Question judge judge_many
"""Moved: ``doeff_system_one.programs`` is now ``doeff_system_one._programs`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_system_one.programs…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_system_one._programs import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_system_one._programs", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_system_one._programs")
