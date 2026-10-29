# moved-submodule-shim: doeff_ml_nexus.uv_deps -> doeff_ml_nexus._uv_deps
# moved-submodule-shim-names doeff_ml_nexus.uv_deps:
#   LocalDep find_local_deps rewrite_pyproject_for_container rewrite_uv_lock_for_container
"""Moved: ``doeff_ml_nexus.uv_deps`` is now ``doeff_ml_nexus._uv_deps`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_ml_nexus.uv_deps…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_ml_nexus._uv_deps", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_ml_nexus._uv_deps")
