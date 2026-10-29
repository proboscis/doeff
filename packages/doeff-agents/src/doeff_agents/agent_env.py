# moved-submodule-shim: doeff_agents.agent_env -> doeff_agents._agent_env
"""Moved: ``doeff_agents.agent_env`` is now ``doeff_agents._agent_env`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_agents.agent_env…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

import hy  # noqa: F401  # the moved module is Hy: load Hy's import hook first

if TYPE_CHECKING:
    from doeff_agents._agent_env import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_agents._agent_env", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_agents._agent_env")
