# moved-submodule-shim: doeff_ml_nexus.serializer -> doeff_ml_nexus._serializer
# moved-submodule-shim-names doeff_ml_nexus.serializer:
#   CloudpickleSerializer Serializer default_serializer
"""Moved: ``doeff_ml_nexus.serializer`` is now ``doeff_ml_nexus._serializer`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_ml_nexus.serializer…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_ml_nexus._serializer", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_ml_nexus._serializer")
