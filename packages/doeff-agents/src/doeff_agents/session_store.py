# moved-submodule-shim: doeff_agents.session_store -> doeff_agents._session_store
# moved-submodule-shim-names doeff_agents.session_store:
#   AgentSessionEvent AgentSessionQuery AgentSessionRepository AgentSessionSnapshot
#   InMemoryAgentSessionRepository IoGenerator IoRoot JsonlAgentSessionRepository append_text
#   as_optional_str as_str_tuple list_dir list_snapshots_program make_dirs read_snapshot_program
#   read_text record_snapshot_program write_text
"""Moved: ``doeff_agents.session_store`` is now ``doeff_agents._session_store`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_agents.session_store…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_agents._session_store import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_agents._session_store", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_agents._session_store")
