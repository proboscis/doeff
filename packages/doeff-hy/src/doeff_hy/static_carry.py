# moved-submodule-shim: doeff_hy.static_carry -> doeff_hy._static_carry
# moved-submodule-shim-names doeff_hy.static_carry:
#   CARRY_VERSION Candidate CarriedFile CarriedRow CarryRecord CarryUnreadableError Checking
#   DEFAULT_REFRESH_RUNS Environment Facts FileInputs FileRows Imported InputGraph MISMATCH_SHOWN
#   Place Places Plan REASONS ReasonCount Resolution Statement Summary carry_text chains_of
#   module_facts next_record own_digest places_of plan_carry probe_environment read_carry
#   semantics_digest write_carry
"""Moved: ``doeff_hy.static_carry`` is now ``doeff_hy._static_carry`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.static_carry…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy._static_carry import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._static_carry", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._static_carry")
