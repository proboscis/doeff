# moved-submodule-shim: doeff_records.values -> doeff_records._values
# moved-submodule-shim-names doeff_records.values:
#   AppendEventAnswer AppendEventsAnswer Appended AppendedEvents BatchRead ByKeyHead ByKeySuffix
#   Changes Conflict DescendingWithin EachEvent Event EventAbsent EventRetired Events EventsMoved
#   EventsQuiet ExpectAbsent ExpectAny ExpectVersion Expectation FIELD_NAME_PATTERN FieldDecl
#   KeepFor KeepForever KeyMemory ListCursor ListRowsAnswer Missing NotIndexed Page PutRowAnswer
#   PutRowsAnswer ReadBatchAnswer ReadEventByKeyAnswer ReadEventsAnswer ReadRowAnswer
#   ReadStreamEndAnswer RecordsSchema Refused RememberKeysFor RememberKeysForever RememberNoKeys
#   Reset Retention RetentionGroup Row RowChanged RowRemoved RowsConflict
#   RowsRefused SEPARATOR_PATTERN StreamDecl StreamEmpty StreamEnd StreamTail StreamTailEmpty
#   TABLE_NAME_PATTERN TableDecl UndeclaredField UndeclaredTable Unreachable WRITE_KEY_MAX_LENGTH
#   WRITE_KEY_MEMORY_SECONDS WaitsClosed WatchChangesAnswer WatchCursor WatchEventsAnswer Written
#   WrittenRows checked_field_name checked_names checked_table_name checked_write_key freeze_field
#   key_memory_ms
"""Moved: ``doeff_records.values`` is now ``doeff_records._values`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_records.values…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_records._values import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_records._values", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_records._values")
