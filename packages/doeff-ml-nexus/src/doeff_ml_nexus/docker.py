# moved-submodule-shim: doeff_ml_nexus.docker -> doeff_ml_nexus._docker
# moved-submodule-shim-names doeff_ml_nexus.docker:
#   Resolve RsyncTo apt_install copy_from copy_local_deps copy_resolved find_local_deps
#   has_rust_extension install_rust install_uv uv_gpu_image uv_image uv_image_core
"""Moved: ``doeff_ml_nexus.docker`` is now ``doeff_ml_nexus._docker`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_ml_nexus.docker…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_ml_nexus._docker", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_ml_nexus._docker")
