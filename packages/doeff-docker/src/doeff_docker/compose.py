# moved-submodule-shim: doeff_docker.compose -> doeff_docker._compose
"""Moved: ``doeff_docker.compose`` is now ``doeff_docker._compose`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_docker.compose…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_docker._compose", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_docker._compose")
