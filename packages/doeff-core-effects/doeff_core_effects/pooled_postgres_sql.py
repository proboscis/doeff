# moved-submodule-shim: doeff_core_effects.pooled_postgres_sql -> doeff_core_effects._pooled_postgres_sql
# moved-submodule-shim-names doeff_core_effects.pooled_postgres_sql:
#   AcquireSemaphore CreateSemaphore PostgresConnections ReleaseSemaphore SqlBatch SqlDropNotice
#   SqlEnsureTables SqlHangNotice SqlInsertRows SqlNotify SqlQuery SqlTransaction hung_notice
#   notified offloaded_statement offloaded_transaction permit_for permitted
#   pooled_postgres_sql_handler postgres_ensure_tables postgres_insert postgres_query stray_batch
#   transaction_locks
"""Moved:``doeff_core_effects.pooled_postgres_sql`` is now ``doeff_core_effects._pooled_postgres_sql`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.pooled_postgres_sql…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._pooled_postgres_sql import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._pooled_postgres_sql", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._pooled_postgres_sql")
