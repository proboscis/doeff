# moved-submodule-shim: doeff_validation.failures -> doeff_validation._failures
# moved-submodule-shim-names doeff_validation.failures:
#   Check CheckArgument CheckError CheckFailure EvaluatedArgument ItemFailedError Perform
#   ValidationException
"""Moved: ``doeff_validation.failures`` is now ``doeff_validation._failures`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_validation.failures…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_validation._failures import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_validation._failures", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_validation._failures")
