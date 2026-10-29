# moved-submodule-shim: doeff_conductor.monitor -> doeff_conductor._monitor
# moved-submodule-shim-names doeff_conductor.monitor:
#   AgentJournal NodeView PROGRESS_STATUS_FAILED PROGRESS_STATUS_PARKED PROGRESS_STATUS_RUNNING
#   PROGRESS_STATUS_SUCCEEDED ProgressJournal RunRow STATUS_DONE STATUS_PENDING
#   TERMINAL_KIND_SUCCEEDED VIEWS capture_agent_output list_open_gates node_identity_fingerprint
#   node_situation node_status_map render_dashboard render_overview render_run render_run_tree
#   run_rows
"""Moved: ``doeff_conductor.monitor`` is now ``doeff_conductor._monitor`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_conductor.monitor…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_conductor._monitor import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_conductor._monitor", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_conductor._monitor")
