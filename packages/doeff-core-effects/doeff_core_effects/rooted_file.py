# moved-submodule-shim: doeff_core_effects.rooted_file -> doeff_core_effects._rooted_file
# moved-submodule-shim-names doeff_core_effects.rooted_file:
#   ANSWER AcquireLock AppendBytes AppendText Appended CompilePythonSources CopyFile CopyTree
#   DiskUsage FileFailed LinkFile ListDirectory LockHeld MODULE_TAGS MOVE_EFFECTS MakeDirectory
#   MakeSymlink MeasureTree PATH_EFFECTS PathStat ReadBytes ReadDiskFree ReadDiskUsage ReadText
#   ReleaseLock RemoveTree RenamePath StatPath StatTree SyncDirectory TruncateFile WalkTree
#   WriteBytes WriteText inner_answer inner_detail inner_path moved outer_path rooted_file_handler
#   unchanged
"""Moved: ``doeff_core_effects.rooted_file`` is now ``doeff_core_effects._rooted_file`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.rooted_file…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._rooted_file import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._rooted_file", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._rooted_file")
