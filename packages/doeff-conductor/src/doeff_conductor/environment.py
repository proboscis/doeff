# moved-submodule-shim: doeff_conductor.environment -> doeff_conductor._environment
# moved-submodule-shim-names doeff_conductor.environment:
#   DEFAULT_PROFILE_DATA DEFAULT_ROLE_CONVENTIONS DEFAULT_ROUTER_POLICY DEFAULT_SITE_CAPABILITIES
#   ProfileBinding ProfileRegistry ResolvedIdentity default_profile_name describe_author_environment
#   load_profile_registry_from_env profile_config_path profiles_json resolved_identity_fingerprint
"""Moved: ``doeff_conductor.environment`` is now ``doeff_conductor._environment`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_conductor.environment…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_conductor._environment import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_conductor._environment", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_conductor._environment")
