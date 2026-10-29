# moved-submodule-shim: doeff_hy.match_fields -> doeff_hy._match_fields
# moved-submodule-shim-names doeff_hy.match_fields:
#   mangle_match_fields
"""Moved: ``doeff_hy.match_fields`` is now ``doeff_hy._match_fields`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.match_fields…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy._match_fields import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._match_fields", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._match_fields")
