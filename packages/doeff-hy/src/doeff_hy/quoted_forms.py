# moved-submodule-shim: doeff_hy.quoted_forms -> doeff_hy._quoted_forms
# moved-submodule-shim-names doeff_hy.quoted_forms:
#   QuotedForms
"""Moved: ``doeff_hy.quoted_forms`` is now ``doeff_hy._quoted_forms`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.quoted_forms…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy._quoted_forms import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._quoted_forms", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._quoted_forms")
