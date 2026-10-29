# moved-submodule-shim: doeff_adr.registry -> doeff_adr._registry
# moved-submodule-shim-names doeff_adr.registry:
#   ADR_STATUSES AdrSpec AdrStatus ENFORCEMENT_MODES EnforcementMode EnforcementRef FixtureSpec
#   SemgrepResult SemgrepSpec clear_registry enforcement_ids enforcement_ref fixture_set_key
#   get_enforcement isolated_registry make_counterexample make_fact make_interpretation make_law
#   make_rule read_verdict register_adr resolved_config_path semgrep_identity verdict_store
#   write_verdicts
"""Moved:``doeff_adr.registry`` is now ``doeff_adr._registry`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_adr.registry…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_adr._registry import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_adr._registry", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_adr._registry")
