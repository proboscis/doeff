# moved-submodule-shim: doeff_hy.positions -> doeff_hy._positions
"""Moved: ``doeff_hy.positions`` is now ``doeff_hy._positions`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.positions…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._positions", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._positions")
