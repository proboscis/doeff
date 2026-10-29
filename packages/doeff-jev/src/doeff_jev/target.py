# moved-submodule-shim: doeff_jev.target -> doeff_jev._target
# moved-submodule-shim-names doeff_jev.target:
#   CONFIG_FILE DIRECT_KEY_FILE DIRECT_MODEL DIRECT_URL GATEWAY_HOST GATEWAY_KEY_FILE GATEWAY_MODEL
#   GATEWAY_URL JevTarget ReadText TYPESAFE_HOST Wire key_required read_text_from_disk
#   resolve_target target_from_process_environment
"""Moved: ``doeff_jev.target`` is now ``doeff_jev._target`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_jev.target…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_jev._target import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_jev._target", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_jev._target")
