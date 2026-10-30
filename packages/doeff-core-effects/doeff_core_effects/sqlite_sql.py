# moved-submodule-shim: doeff_core_effects.sqlite_sql -> doeff_core_effects._sqlite_sql
# moved-submodule-shim-names doeff_core_effects.sqlite_sql:
#   BIND_CLASS_SQLSTATES BIND_ERRORS BUSY_SQLSTATE OPERATIONAL_SQLSTATE SQLITE_CLASS_SQLSTATES
#   SQLITE_TYPES SetSqlOutage SqlBatch SqlColumnType SqlEnsureTables SqlFailed SqlInsertRows
#   SqlParam SqlPlaceholder SqlQuery SqlRows SqlSchemaApplied SqlTable SqlText SqlTransaction
#   SqlUnreachable SqlValue SqliteConnection SqliteStatement TransactionFlush checked_identifier
#   checked_identifiers checked_params checked_rows connection_of normalized_rows outage_marked
#   outage_of param_value run_in_batched_transaction run_in_transaction split_statement
#   sqlite_answer_insert sqlite_answer_query sqlite_answer_tables sqlite_answer_transaction
#   sqlite_connection sqlite_control sqlite_ensure_tables sqlite_failure sqlite_flush sqlite_insert
#   sqlite_query sqlite_run sqlite_schema_statements sqlite_sql_handler sqlite_statement stray_batch
#   with_connection
"""Moved:``doeff_core_effects.sqlite_sql`` is now ``doeff_core_effects._sqlite_sql`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.sqlite_sql…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._sqlite_sql import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._sqlite_sql", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._sqlite_sql")
