# moved-submodule-shim: doeff_records.typed -> doeff_records._typed
# moved-submodule-shim-names doeff_records.typed:
#   Conflict DEFAULT_LIST_LIMIT ExpectAbsent ExpectAny ExpectVersion ListCursor ListRows M
#   MODULE_TAGS Missing Page PutRow ReadRow Row RowChanged RowRemoved RowType TypedConflict
#   TypedPage TypedRow TypedRowChanged TypedWritten WatchCursor Written checked_table_name
#   fields_of_value list_typed model_field_names put_typed read_typed typed_change typed_current
#   typed_row value_of_fields
"""Moved: ``doeff_records.typed`` is now ``doeff_records._typed`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_records.typed…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_records._typed import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_records._typed", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_records._typed")
