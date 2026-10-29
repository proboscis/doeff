# moved-submodule-shim: doeff_records.effects -> doeff_records._effects
# moved-submodule-shim-names doeff_records.effects:
#   AppendEvent AppendEvents Appended AwaitRecordsBack Changes DEFAULT_LIST_LIMIT
#   DEFAULT_READ_EVENTS_LIMIT DEFAULT_WATCH_LIMIT Event EventAbsent EventAppend EventRetired Events
#   EventsMoved EventsQuiet ExpectAbsent ExpectAny ExpectVersion LIST_LIMIT_MAX ListCursor ListRows
#   NotIndexed PutRow PutRows ReadBatch ReadEventByKey ReadEvents ReadRequestPatience ReadRow
#   ReadSignalSource ReadSourcePatience ReadStreamEnd Refused Reset RowWrite RowsConflict
#   RowsRefused StreamEmpty StreamEnd Unreachable WHERE_ANY_LIMIT WatchChanges WatchCursor
#   WatchEvents WrittenRows check_event_append check_row_write checked_event_append
#   checked_event_key checked_field_name checked_key checked_limit checked_put_rows
#   checked_read_window checked_table_name checked_watch_changes checked_watch_window
#   checked_write_key freeze_field write_parts
"""Moved: ``doeff_records.effects`` is now ``doeff_records._effects`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_records.effects…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_records._effects import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_records._effects", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_records._effects")
