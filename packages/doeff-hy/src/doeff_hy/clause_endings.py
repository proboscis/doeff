# moved-submodule-shim: doeff_hy.clause_endings -> doeff_hy._clause_endings
# moved-submodule-shim-names doeff_hy.clause_endings:
#   CHECKED_DEFINITIONS ClauseEndingError ClauseOptions Ending MatchArm NAMED_RESUMPTION
#   check_clause_by_name check_clause_endings check_clause_endings_once clause_op clause_ops
#   effect_name ending_spec_form fell_through hyx_irrefutable_patternXquestion_markX
#   hyx_match_terminatesXquestion_markX hyx_try_terminatesXquestion_markX match_arms ops_ending
#   ops_in parse_clause_options
"""Moved: ``doeff_hy.clause_endings`` is now ``doeff_hy._clause_endings`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.clause_endings…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._clause_endings", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._clause_endings")
