# moved-submodule-shim: doeff_agents.io_effects -> doeff_agents._io_effects
# moved-submodule-shim-names doeff_agents.io_effects:
#   AppendText CopyFile EnvValue HomePath ListDir MakeDirs MonotonicTime PathExists ProcessId
#   ReadText SpawnDetached StartSessionHost TempRoot TouchFile UnixConnectProbe UnixLineRequest
#   WhichExecutable WriteText append_text copy_file env_value executable_at home_path list_dir
#   make_dirs monotonic_time path_exists process_id read_text run_process spawn_detached temp_root
#   touch_file unix_connect_probe unix_line_request which_executable write_text
"""Moved: ``doeff_agents.io_effects`` is now ``doeff_agents._io_effects`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_agents.io_effects…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

import hy  # noqa: F401  # the moved module is Hy: load Hy's import hook first

if TYPE_CHECKING:
    from doeff_agents._io_effects import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_agents._io_effects", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_agents._io_effects")
