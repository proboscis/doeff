# moved-submodule-shim: doeff_records.http_server -> doeff_records._http_server
# moved-submodule-shim-names doeff_records.http_server:
#   ANSWER_METRICS ANSWER_METRIC_HELPS AppendEvent AppendEvents BODY_METHODS BodyWrapper
#   CALLER_HEADER COMPUTE_METRIC_HELPS COMPUTE_QUEUED_METRIC COMPUTE_RUNNING_METRIC
#   COMPUTE_RUN_METRIC COMPUTE_WAIT_METRIC COMPUTE_WORKERS_METRIC CloseWaits ClosingWaits
#   ERROR_INTERNAL ERROR_MALFORMED ERROR_STORE_UNAVAILABLE HttpAnswer HttpRequest IDEMPOTENCY_HEADER
#   JSON_CONTENT_TYPE KeepPreparedHandlers ListRows MAINTAINER METRIC_HELPS MODULE_TAGS
#   MaintenancePlan MarkAt MarkBook NOT_PREPARED_REASON NotIndexed PATH_METRICS PATH_READYZ
#   PATH_SERVED Page PreparedHandlers PressureUnread PutRow PutRows READINESS_SECONDS
#   REQUEST_MAX_BYTES ReadBatch ReadEventByKey ReadEvents ReadRow ReadStreamEnd RecordsListening
#   RecordsPrepared RecordsSchema RecordsServerConfig RecordsService RecordsServing RepoCommit
#   RequestLedger RequestMark Reset RunningServer SERVER_TIMING_HEADER SHELL_CLOSE_SECONDS
#   SHELL_COMPUTE_WORKERS SHELL_OPEN_SECONDS SHELL_STOP_POLL_SECONDS SHELL_STOP_REASON
#   STAGE_METRIC_HELPS STAGE_METRIC_NAMES ServedBuild ServingEnded StageSeconds StampRequest
#   StorePrepared StorePressure StoreReach StoreReachable StoreSilent StoreUnreachable TextAnswer
#   Unreachable WAITING_EFFECTS WRITER_HEADER WaitsClosed WatchChanges WatchEvents answer_arrival
#   answer_metric answer_with cancelled_and_settled closing_cuts_waits count_list_rows deliver
#   header_value json_answer list_rows_caller list_rows_counts list_rows_metric_helps
#   list_rows_metrics lock_metric_helps maintain maintenance_loop metered_body metrics_answer
#   operation_kind operation_of place_answer_metrics prepare_store prepared_slot readiness_answer
#   readiness_timer ready_answer receive_requests records_server_config records_service
#   refusal_answer request_body request_stages request_stamps respond schema_digests send_answer
#   sent_answer sent_stages serve_records served_answer server_timing_spans_of server_timing_spelled
#   serving_body shell_control shell_run stage_observations stamped_handling start_records_server
#   store_not_prepared store_pressure store_probe store_reach timing_headers unsent_streams
#   wait_stamps waited waits_closing watch_stop with_compute_load
"""Moved: ``doeff_records.http_server`` is now ``doeff_records._http_server`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_records.http_server…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_records._http_server import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_records._http_server", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_records._http_server")
