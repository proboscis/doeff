# moved-submodule-shim: doeff_adr.semgrep_verdicts -> doeff_adr._semgrep_verdicts
# moved-submodule-shim-names doeff_adr.semgrep_verdicts:
#   MalformedVerdictError SEMGREP_VERDICTS_ENV VERDICT_FORMAT environment_value fixture_set_key
#   read_verdict semgrep_identity verdict_store write_verdicts
"""Moved:``doeff_adr.semgrep_verdicts`` is now ``doeff_adr._semgrep_verdicts`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_adr.semgrep_verdicts…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_adr._semgrep_verdicts import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_adr._semgrep_verdicts", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_adr._semgrep_verdicts")
