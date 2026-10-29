# moved-submodule-shim: doeff_docker.effects -> doeff_docker._effects
# moved-submodule-shim-names doeff_docker.effects:
#   Copy DockerBuild DockerRun Expose From ImagePush Run SetEnv ShellRun ShellRunResult Workdir
"""Moved: ``doeff_docker.effects`` is now ``doeff_docker._effects`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_docker.effects…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_docker._effects", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_docker._effects")
