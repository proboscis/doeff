# moved-submodule-shim: doeff_core_effects.os_file -> doeff_core_effects._os_file
# moved-submodule-shim-names doeff_core_effects.os_file:
#   AcquireLock AppendBytes AppendText Appended CompilePythonSources CopyFile CopyTree DirEntry
#   DiskUsage EntryStat FILE_THREADS FileFailed IOPRIO_CLASS_IDLE IOPRIO_CLASS_SHIFT
#   IOPRIO_SET_SYSCALL IOPRIO_WHO_PROCESS KIND_OF_FORMAT LOCK_THREADS LinkFile ListDirectory
#   LockHeld MODULE_TAGS MakeDirectory MakeSymlink MeasureTree PathKind PathStat ReadBytes
#   ReadDiskFree ReadDiskUsage ReadText ReleaseLock RemoveTree RenamePath StatPath StatTree
#   SyncDirectory TREE_THREADS ThreadPerCall TruncateFile WalkTree WriteBytes WriteText acquire_lock
#   append_bytes compile_python_sources darwin_volume_free disk_free disk_usage entry_of failed
#   free_reader_for guarded keep_nothing kind_of_mode list_directory lower_io_priority
#   make_directory measure_tree offloaded offloaded_file_handler offloaded_lock_handler
#   offloaded_tree_handler os_file_handler read_file release_abandoned remove_tree reread_free
#   run_detached stat_path stat_tree sync_directory truncate_file walk_tree write_content
"""Moved: ``doeff_core_effects.os_file`` is now ``doeff_core_effects._os_file`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.os_file…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._os_file import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._os_file", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._os_file")
