# moved-submodule-shim: doeff_git.exceptions -> doeff_git._exceptions
# moved-submodule-shim-names doeff_git.exceptions:
#   GitCommandError GitError
"""Moved: ``doeff_git.exceptions`` is now ``doeff_git._exceptions`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_git.exceptions…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_git._exceptions import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_git._exceptions", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_git._exceptions")
