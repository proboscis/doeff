# moved-submodule-shim: doeff_agentic.handler -> doeff_agentic._handler
"""Moved: ``doeff_agentic.handler`` is now ``doeff_agentic._handler`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_agentic.handler…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_agentic._handler import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_agentic._handler", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_agentic._handler")
