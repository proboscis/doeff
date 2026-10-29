# moved-submodule-shim: doeff_hy.outcome_forms -> doeff_hy._outcome_forms
"""Moved: ``doeff_hy.outcome_forms`` is now ``doeff_hy._outcome_forms`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.outcome_forms…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._outcome_forms", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._outcome_forms")
