# moved-submodule-shim: doeff_records.main -> doeff_records._main
# moved-submodule-shim-names doeff_records.main:
#   COMPUTE_WORKERS DATABASE DDL_DATABASE DDL_TIMEOUTS DEFAULT_HOST DEFAULT_KEEP_CHANGES_SECONDS
#   DEFAULT_MAINTENANCE_SECONDS DEFAULT_POOL_SIZE DEFAULT_PORT DEFAULT_PREFIX DRAIN_SECONDS
#   ENV_CALLERS ENV_HOST ENV_HOSTNAME ENV_KEEP_CHANGES_SECONDS ENV_MAINTENANCE_SECONDS
#   ENV_ORIGIN_HOST ENV_PG_URL_FILE ENV_POOL_SIZE ENV_PORT ENV_PREFIX MODULE_TAGS MaintenancePlan
#   PG_STORE PRESSURE_SQL PressureUnread REQUEST_MAX_BYTES RecordsListening RecordsPrepared
#   RecordsSchema RecordsServing RecordsSettings STOP_POLL_SECONDS StoreChoice StorePressure
#   WRITER_HANDLERS_KEPT env_number env_text origin_host pg_handlers_of pg_records_handler
#   pg_url_file_dsn prepare_records_store printed_listening read_secret reconcile_indexes
#   records_connected records_foundation records_process records_serving records_settings
#   required_env serve_records serve_records_service store_pressure_of_rows store_pressure_pg
#   store_reachable writer_gates
"""Moved: ``doeff_records.main`` is now ``doeff_records._main`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_records.main…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_records._main import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_records._main", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_records._main")
