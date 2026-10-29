# moved-submodule-shim: doeff_ml_nexus.runner -> doeff_ml_nexus._runner
# moved-submodule-shim-names doeff_ml_nexus.runner:
#   RUNNER_INPUT_PATH_KEY RUNNER_OUTPUT_PATH_KEY default_serializer get_exchange_paths p_run
#   resolve_runner_env run_from_file runner_interpreter
"""Moved: ``doeff_ml_nexus.runner`` is now ``doeff_ml_nexus._runner`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_ml_nexus.runner…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_ml_nexus._runner", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_ml_nexus._runner")
