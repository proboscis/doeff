# moved-submodule-shim: doeff_records.http_client -> doeff_records._http_client
# moved-submodule-shim-names doeff_records.http_client:
#   AppendEvent AppendEvents CALLER_HEADER CLIENT_ANSWER_METRICS CLIENT_UNREACHABLE Changes
#   DEFAULT_REQUEST_TIMEOUT EXCHANGE_LOG EventsMoved EventsQuiet IDEMPOTENCY_HEADER ListRows
#   MODULE_TAGS PATH_PREFIX PublicEffect PutRow PutRows RECORDS_SIGNAL_SOURCE RawReply ReadBatch
#   ReadEventByKey ReadEvents ReadRequestPatience ReadRow ReadSignalSource ReadStreamEnd
#   RecordsEndpoint RequestPatience Reset SERVER_TIMING_HEADER SERVER_TIMING_QUEUE Unreachable
#   WATCH_MAX_SECONDS WRITER_HEADER WRITE_KEY_BYTES WatchChanges WatchEvents WireAnswer WireError
#   answer_for_request answered_riding_stall call_service came_back_within client_answer_metric
#   client_status_outcome counted_reply decode_answer encode_request exchange exchange_by_effect
#   first_seen fresh_write_key http_records_handler http_table_records_handler logged_exchange
#   long_poll ms_of records_unwaited refusal_from reply_answer reply_json request_bytes
#   request_headers request_key request_patience_handler server_timing_spans service_url stall_names
#   tables_named undeclared_refusal waited_seconds zero_client_metrics
"""Moved: ``doeff_records.http_client`` is now ``doeff_records._http_client`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_records.http_client…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_records._http_client import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_records._http_client", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_records._http_client")
