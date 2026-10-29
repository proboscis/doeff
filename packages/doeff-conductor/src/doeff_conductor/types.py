# moved-submodule-shim: doeff_conductor.types -> doeff_conductor._types
# moved-submodule-shim-names doeff_conductor.types:
#   AgentRef ExecResult Issue IssueStatus MergeConflict MergeStatus MergeStrategy
#   MergeWorkspacesResult PRHandle WorkflowHandle WorkflowStatus Workspace
"""Moved: ``doeff_conductor.types`` is now ``doeff_conductor._types`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_conductor.types…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_conductor._types import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_conductor._types", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_conductor._types")
