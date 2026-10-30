# moved-submodule-shim: doeff_core_effects.postgres_sql -> doeff_core_effects._postgres_sql
# moved-submodule-shim-names doeff_core_effects.postgres_sql:
#   CARRIED_LARGE CreateExternalPromise DEFAULT_POOL_SIZE DEFAULT_TIMEOUTS DRIVER_CLASS_SQLSTATES
#   DRIVER_THREADS ExternalPromise LARGE_CARRY_BYTES LISTEN_RETRY_SECONDS LOCK_SHARED_STATEMENT
#   LOCK_STATEMENT MODULE_TAGS NOTICE_PAYLOAD_LIMIT NOTICE_STATEMENT NoticeWire POSTGRES_TYPES
#   PREPARED PREPARED_LOCK PREPARED_MAX PipelineConnection PostgresConnections PostgresDatabase
#   PostgresListener PostgresStatement PostgresStep PostgresTimeouts PreparedBook RaisedNotices
#   SqlBatch SqlColumnType SqlDropNotice SqlEnsureTables SqlFailed SqlHangNotice SqlInsertRows
#   SqlLock SqlNotify SqlParam SqlQuery SqlRows SqlSchemaApplied
#   SqlTransaction SqlUnreachable StatementCursor TaskCancelledError TransactionAbandoned
#   TransactionDriver TransactionFlush TransactionLease UNREACHABLE_CLASSES UNREACHABLE_SQLSTATES
#   UNREACHABLE_SQLSTATE_CLASS abandon_lease abandoned_now cell_bytes checked_identifier
#   checked_identifiers checked_params checked_rows drive_leased driven flush_steps flushed_here
#   heard_notice hung_notice keep_nothing largest_message_bytes lease_now lease_run_return
#   leased_here libpq_results libpq_sent lock_text normalized_rows noted_carry noted_notice
#   notice_params notice_payload notified offloaded offloaded_batched_transaction
#   offloaded_statement offloaded_transaction postgres_begin postgres_control postgres_ensure_tables
#   postgres_error postgres_failure postgres_flush postgres_insert postgres_insert_statement
#   postgres_lease postgres_notice postgres_query postgres_run postgres_schema_statements
#   postgres_sql_handler postgres_statement prepared_book raised_commit raised_notice request_step
#   result_failure results_answer rolled_back_here run_batched_here run_detached
#   run_in_batched_transaction run_in_transaction socket_ready split_statement step_queries
#   step_sent_bytes stray_batch transaction_locks
"""Moved:``doeff_core_effects.postgres_sql`` is now ``doeff_core_effects._postgres_sql`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.postgres_sql…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._postgres_sql import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._postgres_sql", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._postgres_sql")
