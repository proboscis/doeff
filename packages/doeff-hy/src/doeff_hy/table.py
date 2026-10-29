# moved-submodule-shim: doeff_hy.table -> doeff_hy._table
# moved-submodule-shim-names doeff_hy.table:
#   COMPACT_RATIO MIN_DELTA TABLE_LEN_REFUSAL TABLE_MAPPING_EQ_REFUSAL TABLE_TRUTH_REFUSAL Table
#   TableDraft TableWrite V draft_of table_of
"""Moved: ``doeff_hy.table`` is now ``doeff_hy._table`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.table…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy._table import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._table", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._table")
