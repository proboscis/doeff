# moved-submodule-shim: doeff_conductor.replay_keying -> doeff_conductor._replay_keying
# moved-submodule-shim-names doeff_conductor.replay_keying:
#   ResolvedIdentity agent_cache_key longest_valid_prefix node_identity_fingerprint
#   resolved_identity_fingerprint workflow_effect_cache_key
"""Moved: ``doeff_conductor.replay_keying`` is now ``doeff_conductor._replay_keying`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_conductor.replay_keying…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_conductor._replay_keying import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_conductor._replay_keying", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_conductor._replay_keying")
