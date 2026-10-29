# moved-submodule-shim: doeff_agentic.runtime -> doeff_agentic._runtime
# moved-submodule-shim-names doeff_agentic.runtime:
#   with_handlers
"""Moved: ``doeff_agentic.runtime`` is now ``doeff_agentic._runtime`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_agentic.runtime…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_agentic._runtime import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_agentic._runtime", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_agentic._runtime")
