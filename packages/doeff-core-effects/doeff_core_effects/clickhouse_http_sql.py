# moved-submodule-shim: doeff_core_effects.clickhouse_http_sql -> doeff_core_effects._clickhouse_http_sql
# moved-submodule-shim-names doeff_core_effects.clickhouse_http_sql:
#   ANSWER_FORMAT CLICKHOUSE_COLUMN_TYPES CLICKHOUSE_THREADS CLICKHOUSE_TYPES ClickHouseDatabase
#   ClickHouseHeader ClickHouseKill ClickHouseParam ClickHouseRequest ClickHouseResponse
#   ClickHouseStatement ClickHouseWaits ESCAPES EXIT_KILL_GRACE_SECONDS EXIT_SETTLE_SECONDS
#   KILL_GRACE_SECONDS NOT_SUPPORTED_SQLSTATE QUERY_ID_SHAPE QUERY_SETTINGS ROUND_TRIPS RoundTrips
#   SettleThenTerminate SqlColumnType SqlEnsureTables SqlFailed SqlInsertRows SqlPlaceholder
#   SqlQuery SqlRows SqlSchemaApplied SqlText SqlTransaction SqlUnreachable TIMEOUT_OVERFLOW_MODE
#   ThreadPerCall USER_AGENT WAIT_MARGIN_SECONDS answer_started checked_identifier
#   checked_identifiers checked_params checked_rows clickhouse_abandon clickhouse_answer
#   clickhouse_connection clickhouse_deadline_pairs clickhouse_ensure_tables clickhouse_exit_kill
#   clickhouse_failure clickhouse_headers clickhouse_http_sql_handler clickhouse_insert_request
#   clickhouse_kill clickhouse_kill_request clickhouse_param_text clickhouse_path clickhouse_post
#   clickhouse_query_request clickhouse_read clickhouse_rows clickhouse_schema_statements
#   clickhouse_seconds_text clickhouse_send clickhouse_statement clickhouse_type clickhouse_url
#   clickhouse_waits clickhouse_written_rows install_exit_settle keep_nothing new_query_id
#   normalized_rows off_scheduler offloaded param_value run_detached settle_at_exit
#   settle_round_trips split_statement submit_exit_kill
"""Moved:``doeff_core_effects.clickhouse_http_sql`` is now ``doeff_core_effects._clickhouse_http_sql`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.clickhouse_http_sql…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._clickhouse_http_sql import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._clickhouse_http_sql", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._clickhouse_http_sql")
