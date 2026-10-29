# moved-submodule-shim: doeff_ml_nexus.runner_env -> doeff_ml_nexus._runner_env
# moved-submodule-shim-names doeff_ml_nexus.runner_env:
#   DEFAULT_RUNNER_INPUT_PATH DEFAULT_RUNNER_OUTPUT_PATH RUNNER_INPUT_PATH_KEY
#   RUNNER_OUTPUT_PATH_KEY default_runner_env resolve_runner_env runner_env_from_process
"""Moved: ``doeff_ml_nexus.runner_env`` is now ``doeff_ml_nexus._runner_env`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_ml_nexus.runner_env…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_ml_nexus._runner_env", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_ml_nexus._runner_env")
