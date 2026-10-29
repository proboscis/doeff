# moved-submodule-shim: doeff_records.laws -> doeff_records._laws
# moved-submodule-shim-names doeff_records.laws:
#   ANY_OF_CASES ANY_OF_PAGE ANY_OF_ROWS ANY_OF_TOP AdvanceStoreEpoch AppendEvent AppendEvents
#   Appended AppendedEvents BEAT_KEEP_SECONDS BEAT_MEMORY_SECONDS ByKeyHead ByKeySuffix CLOSER
#   COLORS CONCURRENT_ROUNDS CONCURRENT_WRITERS ChangeLog Changes Conflict DESCENDING_PAGE_CAP
#   DescendingWithin Event EventAbsent EventAppend EventRetired Events EventsArchived EventsMoved
#   EventsQuiet ExpectAbsent ExpectAny ExpectVersion ExpiredEvents FOLLOW_LIMIT FOLLOW_READS
#   FieldDecl Followed FollowedEvents KeepFor KeepForever LABELS LAWS LAW_SCHEMA LawBroken
#   LawHarness ListCursor ListRows MAKER MODULE_TAGS Missing NotIndexed OVERSEER PAINTER
#   PAIR_KEEP_SECONDS PULSE_KEEP_SECONDS Page PruneChanges Pruned PutRow PutRows RELAY_KEEP_SECONDS
#   ReadBatch ReadChangeLog ReadEventByKey ReadEvents ReadExpiredEvents ReadRow ReadStreamEnd
#   RecordsSchema Refused RememberKeysFor RememberNoKeys Reset RetireArchivedEvents RetiredEvents
#   Row RowChanged RowRemoved RowWrite RowsConflict RowsRefused SHARED_LAWS SHORT_PAGE_LIMIT
#   STRANGER StreamDecl StreamEmpty StreamEnd StreamTail StreamTailEmpty SweepExpired Swept
#   TICKET_KEEP_SECONDS TOP_NOTES TOP_WHERES TRAIL_HEADS TRAIL_KEYS TableDecl Unreachable WHERES
#   WRITE_KEY_MEMORY_SECONDS WatchChanges WatchCursor WatchEvents Written WrittenRows
#   archive_expired_events as_writer collect_any_pages collect_changes collect_descending_pages
#   collect_pages concurrent_appender concurrent_follower concurrent_row_writer confirm_keys
#   confirm_with_a_late_guess epoch_ms expired_key_answers expired_keys followed_events
#   hyx_fresh_appendXquestion_markX hyx_row_matchesXquestion_markX key_text late_append late_write
#   law_a_keyed_write_replays_its_answer law_a_write_clears_the_expired_row_it_touches
#   law_a_write_keeps_the_unarchived_event_it_touches law_a_write_names_its_change_position
#   law_an_expired_key_answers_the_same_before_and_after_a_sweep
#   law_another_key_is_judged_by_its_expectation law_any_of_list_equals_the_union_of_equal_lists
#   law_append_events_answer_like_appends_in_order law_append_is_idempotent
#   law_change_log_reads_every_change law_committed_changes_appear_once_in_order
#   law_concurrent_writes_appear_in_number_order law_descending_list_is_the_head_of_the_sorted_scan
#   law_descending_pages_follow_the_sorted_scan law_epoch_change_resets
#   law_event_by_key_reads_the_same_event law_expired_keys_are_remembered
#   law_expired_records_are_unseen_before_a_sweep law_grouped_events_expire_together
#   law_indexed_list_equals_filtered_scan law_key_head_read_equals_filtered_scan
#   law_keys_are_forgotten_after_their_memory law_maintenance_prunes_and_sweeps
#   law_none_removes_a_field law_only_confirmed_events_are_retired
#   law_prune_stops_at_the_archived_seq law_put_rows_is_all_or_nothing
#   law_retirement_keeps_the_read_boundary law_short_page_ends_the_stream law_stale_put_conflicts
#   law_stream_end_is_the_last_sequence law_transient_rows_expire
#   law_unarchived_keys_are_kept_until_retired law_undeclared_writes_are_refused
#   law_watch_events_waits_for_an_append law_watch_tails_match_last_events
#   law_watch_waits_for_a_change law_write_keys_are_forgotten_after_their_memory relay_answers
#   replay_of require_law short_page_is_the_end sorted_descending_scan tails_of_last_events
#   union_of_equal_lists unplaced unplaced_all unreachable_destination
"""Moved: ``doeff_records.laws`` is now ``doeff_records._laws`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_records.laws…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_records._laws import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_records._laws", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_records._laws")
