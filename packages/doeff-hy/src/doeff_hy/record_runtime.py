# moved-submodule-shim: doeff_hy.record_runtime -> doeff_hy._record_runtime
# moved-submodule-shim-names doeff_hy.record_runtime:
#   EFFECT_PARAMS FACTORY_DEFAULT RECORD_DOC RECORD_PARAMS record_delattr record_repr record_setattr
#   seal_effect seal_record
"""Moved: ``doeff_hy.record_runtime`` is now ``doeff_hy._record_runtime`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.record_runtime…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy._record_runtime import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._record_runtime", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._record_runtime")
