# moved-submodule-shim: doeff_events.macros -> doeff_events._macros
# moved-submodule-shim-names doeff_events.macros:
#   event_loop
"""Moved: ``doeff_events.macros`` is now ``doeff_events._macros`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_events.macros…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_events._macros", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_events._macros")
