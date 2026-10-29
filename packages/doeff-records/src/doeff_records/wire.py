# moved-submodule-shim: doeff_records.wire -> doeff_records._wire
# moved-submodule-shim-names doeff_records.wire:
#   ANSWER_KINDS ANSWER_METRIC ANSWER_METRICS ANSWER_METRIC_HELPS ANSWER_STATUSES AppendEvent
#   AppendEvents Appended AppendedEvents CALLER_HEADER CALLER_UNDECLARED CALLER_UNNAMED
#   CLIENT_ANSWER_METRIC CLIENT_ANSWER_METRICS CLIENT_KINDS CLIENT_OTHER_STATUS CLIENT_OUTCOMES
#   CLIENT_UNREACHABLE Changes Conflict DecodedRequest ERROR_INTERNAL ERROR_MALFORMED
#   ERROR_NOT_FOUND ERROR_STORE_UNAVAILABLE Event EventAbsent EventAppend EventRetired Events
#   EventsMoved EventsQuiet ExpectAbsent ExpectAny ExpectVersion IDEMPOTENCY_HEADER
#   LIST_ROWS_CALLER_METRIC LIST_ROWS_MEASURES LIST_ROWS_TABLE_METRIC ListCursor ListRows
#   METRIC_WORD_UNSAFE MODULE_TAGS Missing NamedStores NotIndexed OPERATIONS OP_APPEND_EVENT
#   OP_APPEND_EVENTS OP_LIST_ROWS OP_PUT_ROW OP_PUT_ROWS OP_READ_EVENTS OP_READ_EVENT_BY_KEY
#   OP_READ_ROW OP_READ_STREAM_END OP_WATCH_CHANGES OP_WATCH_EVENTS PATH_PREFIX Page PublicEffect
#   PutRow PutRows ReadBatch ReadEventByKey ReadEvents ReadRow ReadStreamEnd Refused RequestKind
#   Reset Row RowChanged RowRemoved RowWrite RowsConflict RowsRefused SERVER_TIMING_HEADER
#   SERVER_TIMING_NAMES SERVER_TIMING_QUEUE STATUS_OF_ERROR ServerSpan StreamEmpty StreamEnd
#   StreamTail StreamTailEmpty UndeclaredTable WATCH_MAX_SECONDS WRITER_HEADER WRITE_OPERATIONS
#   WatchChanges WatchCursor WatchEvents WireAnswer WireMalformed WireRefusal WireRequest Written
#   WrittenRows answer_for_request answer_from answer_metric answer_object_of appended_events_from
#   boolean_of change_from change_json client_answer_metric client_status_outcome decode_answer
#   decode_request encode_answer encode_request event_append_from event_append_json
#   event_appends_from event_from event_json expect_from expect_json integer_of json_object_in
#   keyed_request list_cursor_from list_cursor_json list_in list_rows_caller list_rows_counts
#   list_rows_metric_helps list_rows_metrics malformed metric_word named_stores object_of
#   operation_kind operation_of refusal_from refusal_json request_key request_kind row_from row_json
#   row_write_from row_write_json row_writes_from rows_conflict_from seconds_of server_timing_spans
#   server_timing_spelled shape_of string_of strings_of tail_from tail_json tails_from
#   undeclared_reason undeclared_refusal watch_cursor_from watch_cursor_json written_rows_from
"""Moved: ``doeff_records.wire`` is now ``doeff_records._wire`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_records.wire…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_records._wire import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_records._wire", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_records._wire")
