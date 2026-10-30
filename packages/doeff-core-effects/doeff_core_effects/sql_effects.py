# moved-submodule-shim: doeff_core_effects.sql_effects -> doeff_core_effects._sql_effects
# moved-submodule-shim-names doeff_core_effects.sql_effects:
#   DriverValue ExternalPromise IDENTIFIER MODULE_TAGS PLAIN_VALUE_TYPES SPLIT_MEMO SPLIT_MEMO_LIMIT
#   SQL_VALUE_TYPES SetSqlOutage SqlBatch SqlColumn SqlColumnType SqlDropNotice SqlEnsureTables
#   SqlFailed SqlHangNotice SqlIndex SqlInsertRows SqlLock SqlNotify SqlParam SqlPlaceholder
#   SqlQuery SqlRows SqlSchemaApplied SqlTable SqlText SqlTransaction SqlTransactionMisuse
#   SqlUnreachable SqlValue T TOKEN checked_identifier checked_identifiers checked_params
#   checked_rows is_driver_value is_plain_rows normalized_rows normalized_value param_value
#   split_statement
"""Moved:``doeff_core_effects.sql_effects`` is now ``doeff_core_effects._sql_effects`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.sql_effects…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._sql_effects import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._sql_effects", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._sql_effects")
