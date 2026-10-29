# moved-submodule-shim: doeff_records.memory -> doeff_records._memory
# moved-submodule-shim-names doeff_records.memory:
#   AddStoreFault Admitted AdvanceStoreEpoch AppendEvent AppendEvents AppendNew AppendReplay
#   Appended AppendedEvents AwaitRecordsBack BodyWrapper CLOCK_TICK ChangeLog ChangedRow Changes
#   ClearStoreFaults Conflict Event EventAbsent EventAppend EventDue EventRetired Events EventsMoved
#   EventsQuiet ExpiredCursor ExpiredEvents GroupDue KeepFor ListCursor ListRows
#   MEMORY_SOURCE_EFFECTS MODULE_TAGS MemoryMark MemorySignals MemoryStore Missing NotIndexed Page
#   PruneChanges Pruned PutRow PutRows READ ReadBatch ReadChangeLog ReadEventByKey ReadEvents
#   ReadExpiredEvents ReadRow ReadSignalSource ReadSourcePatience ReadStreamEnd RecordsSchema
#   Refused RememberedWrite Reset RetireArchivedEvents RetiredEvents RetiredKey Row RowChanged
#   RowDue RowRemoved RowsConflict RowsRefused SetStoreOutage SignalSourceFactory SignalTables
#   StoreChoice StoreFault StoreOperation StoredGroup StoredRow StreamDecl StreamEmpty StreamEnd
#   StreamTail StreamTailEmpty SweepExpired Swept TableDecl Unreachable WRITE
#   WRITE_KEY_MEMORY_SECONDS WaitsClosed WatchChanges WatchCursor WatchEvents WatchRound Written
#   WrittenRows answered answered_appends append_faults archived_streams at_now bell_or_timer
#   binding_keys body_digest checked_bindings drop_bell drop_events epoch_ms expired_or_down
#   expired_page_locked failure_announced fault_for first_seen first_seen_faults forget_keys_locked
#   forget_write_keys_locked guarded hang_bell hyx_closing_wakeXquestion_markX
#   hyx_descending_valueXquestion_markX hyx_due_liveXquestion_markX hyx_event_expiredXquestion_markX
#   hyx_hidden_changeXquestion_markX hyx_row_expiredXquestion_markX hyx_row_matchesXquestion_markX
#   hyx_row_matches_anyXquestion_markX hyx_stored_event_expiredXquestion_markX
#   hyx_stored_row_expiredXquestion_markX hyx_terminal_rowXquestion_markX judge_append judge_expect
#   judge_put judge_put_rows judge_write_replay keep_ms key_head_of key_head_refusal key_memory_ms
#   key_text listed_row memory_add_fault memory_advance_epoch memory_append memory_append_events
#   memory_append_one memory_await_back memory_change_log memory_clear_faults memory_current_row
#   memory_events_scan memory_list_rows memory_mark memory_prepared memory_prune_changes
#   memory_publish memory_put_row memory_put_rows memory_reach memory_read_event_by_key
#   memory_read_events memory_read_expired_events memory_read_row memory_read_stream_end
#   memory_readiness memory_records_handler memory_retire_archived_events memory_signal_handler
#   memory_signal_source memory_signals_since memory_spend_fault memory_store_choice
#   memory_store_row memory_sweep memory_watch memory_watch_scan new_store_lock next_purge_due
#   next_watch_sequence note_event_due note_purge_due prune_changes_locked purge_expired
#   purge_expired_locked purge_expired_scan push_due read_at_now remember_events remember_write
#   remove_stored_row retention_group_of retire_archived_locked retire_touched_events ride_out_stall
#   ring_bells run_memory_source settle_departed_keys stop_source unreachable_for wait_span
#   waits_beside_sources watch_round where_refusal writable_row write_digest write_parts
#   write_replay
"""Moved:``doeff_records.memory`` is now ``doeff_records._memory`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_records.memory…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_records._memory import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_records._memory", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_records._memory")
