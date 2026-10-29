# moved-submodule-shim: doeff_hy.clause_ops -> doeff_hy._clause_ops
# moved-submodule-shim-names doeff_hy.clause_ops:
#   CLAUSE_OPS ClauseOp Ending alias_symbol clause_op continuation_symbol effect_symbol import_form
#   linter_table_json named_op node_names ops_ending static_view_enabled
"""Moved: ``doeff_hy.clause_ops`` is now ``doeff_hy._clause_ops`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.clause_ops…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._clause_ops", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._clause_ops")
