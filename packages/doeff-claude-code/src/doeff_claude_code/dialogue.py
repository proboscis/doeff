# moved-submodule-shim: doeff_claude_code.dialogue -> doeff_claude_code._dialogue
"""Moved: ``doeff_claude_code.dialogue`` is now ``doeff_claude_code._dialogue`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_code.dialogue…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_code._dialogue", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_code._dialogue")
