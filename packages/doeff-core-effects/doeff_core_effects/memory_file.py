# moved-submodule-shim: doeff_core_effects.memory_file -> doeff_core_effects._memory_file
# moved-submodule-shim-names doeff_core_effects.memory_file:
#   ANSWER AcquireLock AppendBytes AppendText Appended CompilePythonSources CompletePromise CopyFile
#   CopyTree CreatePromise DirEntry DiskUsage EXISTS EntryStat FileFailed INVALID IS_DIRECTORY LOOP
#   LinkFile ListDirectory LockHeld MAX_HOPS MODULE_TAGS MakeDirectory MakeSymlink MeasureTree
#   MemoryFile MemoryFiles MemoryLink NOT_DIRECTORY NOT_EMPTY NOT_PERMITTED NO_ENTRY PathKind
#   PathStat ROOT ReadBytes ReadDiskFree ReadDiskUsage ReadMemoryFiles ReadText ReleaseLock
#   RemoveTree RenamePath SourceNotCompiled StatPath StatTree SyncDirectory TruncateFile Wait
#   WalkTree WriteBytes WriteText appended_in as_asked compile_in_store compiled_pyc content_of
#   copy_tree_in dir_refusal entries_below hyx_underXquestion_markX kept_pyc kind_in link_at link_in
#   list_in measured_in memory_file_handler moved normal on_pair on_path parent_refusal pyc_path
#   refused refused_move remove_in rename_in resolved return_path return_store stat_in stats_in
#   symlink_in truncated_in walk_in with_dirs with_file without_tree
"""Moved: ``doeff_core_effects.memory_file`` is now ``doeff_core_effects._memory_file`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.memory_file…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._memory_file import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._memory_file", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._memory_file")
