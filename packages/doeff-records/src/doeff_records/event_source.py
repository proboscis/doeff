# moved-submodule-shim: doeff_records.event_source -> doeff_records._event_source
# moved-submodule-shim-names doeff_records.event_source:
#   AwaitRecordsBack BodyWrapper ChangedRow Changes EventsMoved EventsQuiet ListRows MODULE_TAGS
#   NotIndexed Page READ_SIGNAL_EFFECTS RECORDS_SIGNAL_SOURCE ReadSignalSource ReadSourcePatience
#   ReadStreamEnd Reset SOURCE_EFFECTS SignalSourceFactory SignalSourcePatience
#   SignalSourceUnreachable SignalTables SourcePlan StreamEmpty StreamEnd StreamStart Unreachable
#   WATCH_SECONDS WatchChanges WatchCursor WatchEvents back_announced came_back_within changed_rows
#   checked_bindings checked_table_name failure_announced first_seen key_text plan_of
#   publish_appends publish_changes reachable read_signal_handler records_signal_handler
#   ride_out_stall run_read_signal run_signal_source run_with_sources signals_of
#   source_patience_handler spawn_sources start_cursor stop_source stream_starts
#   wait_all_beside_sources wait_beside_sources waits_beside_sources
"""Moved: ``doeff_records.event_source`` is now ``doeff_records._event_source`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_records.event_source…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_records._event_source import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_records._event_source", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_records._event_source")
