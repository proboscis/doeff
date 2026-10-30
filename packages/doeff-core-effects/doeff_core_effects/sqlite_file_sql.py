# moved-submodule-shim: doeff_core_effects.sqlite_file_sql -> doeff_core_effects._sqlite_file_sql
# moved-submodule-shim-names doeff_core_effects.sqlite_file_sql:
#   JOURNAL_MODE SetSqlOutage SqlBatch SqlEnsureTables SqlInsertRows SqlQuery SqlTransaction
#   SqliteConnection SqliteFile SqliteFiles close_sqlite_files connection_of open_sqlite_files
#   outage_marked sqlite_answer_insert sqlite_answer_query sqlite_answer_tables
#   sqlite_answer_transaction sqlite_connection sqlite_file_connection sqlite_file_sql_handler
#   stray_batch
"""Moved:``doeff_core_effects.sqlite_file_sql`` is now ``doeff_core_effects._sqlite_file_sql`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.sqlite_file_sql…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._sqlite_file_sql import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._sqlite_file_sql", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._sqlite_file_sql")
