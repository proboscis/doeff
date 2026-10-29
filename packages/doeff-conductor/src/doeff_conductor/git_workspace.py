# moved-submodule-shim: doeff_conductor.git_workspace -> doeff_conductor._git_workspace
# moved-submodule-shim-names doeff_conductor.git_workspace:
#   GitCommandError GitCommandResult append_git_output conflicted_files get_current_commit
#   get_default_branch get_repo_root run_git
"""Moved: ``doeff_conductor.git_workspace`` is now ``doeff_conductor._git_workspace`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_conductor.git_workspace…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_conductor._git_workspace import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_conductor._git_workspace", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_conductor._git_workspace")
