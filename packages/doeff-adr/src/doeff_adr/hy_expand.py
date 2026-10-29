# moved-submodule-shim: doeff_adr.hy_expand -> doeff_adr._hy_expand
# moved-submodule-shim-names doeff_adr.hy_expand:
#   ExpandedHy HyExpansionError expand_hy_file expand_hy_source module_name_for
"""Moved:``doeff_adr.hy_expand`` is now ``doeff_adr._hy_expand`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_adr.hy_expand…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_adr._hy_expand import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_adr._hy_expand", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_adr._hy_expand")
