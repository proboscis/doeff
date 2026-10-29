# moved-submodule-shim: doeff_conductor.macros -> doeff_conductor._macros
# moved-submodule-shim-names doeff_conductor.macros:
#   <- agent! defphase defworkflow gate! loop merge! parallel parallel_for pipeline random! time!
#   workspace!
"""Moved: ``doeff_conductor.macros`` is now ``doeff_conductor._macros`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_conductor.macros…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_conductor._macros", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_conductor._macros")
