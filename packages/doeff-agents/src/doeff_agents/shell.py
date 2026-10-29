# moved-submodule-shim: doeff_agents.shell -> doeff_agents._shell
# moved-submodule-shim-names doeff_agents.shell:
#   assert_no_forbidden_agent_env assert_session_env_is_non_auth_overlay forbidden_agent_env_keys
#   wrap_with_shell_exports
"""Moved: ``doeff_agents.shell`` is now ``doeff_agents._shell`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_agents.shell…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_agents._shell import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_agents._shell", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_agents._shell")
