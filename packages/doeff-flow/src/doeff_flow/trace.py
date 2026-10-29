# moved-submodule-shim: doeff_flow.trace -> doeff_flow._trace
# moved-submodule-shim-names doeff_flow.trace:
#   GatherState LiveTrace TraceFrame get_default_trace_dir trace_observer validate_workflow_id
#   write_terminal_trace
"""Moved: ``doeff_flow.trace`` is now ``doeff_flow._trace`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_flow.trace…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_flow._trace import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_flow._trace", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_flow._trace")
