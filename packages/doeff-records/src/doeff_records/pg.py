# moved-submodule-shim: doeff_records.pg -> doeff_records._pg
# moved-submodule-shim-names doeff_records.pg:
#   AdmitWriters AdvanceStoreEpoch AppendEvent AppendEvents AppendReads AppendReplay Appended
#   AppendedEvents ByKeySuffix ChangeLog Changes Conflict DEADLOCK_SQLSTATE DEFAULT_PREFIX
#   DeclaredIndex EXPIRE_BATCH EarlierUse Event EventAbsent EventExpiry EventRetired Events
#   EventsMoved EventsQuiet ExpiredCursor ExpiredEvents ExpiredRow HeadRead INDEX_RETRY_SECONDS
#   INDEX_TOPIC IndexBook IndexGap IndexedField InsertAt KeepFor ListCursor ListRows LockedRow
#   MODULE_TAGS Missing NO_STEPS NotIndexed ObservedIndex PREPARE_ATTEMPTS PRUNE_BATCH
#   PRUNE_STATEMENT_MILLISECONDS Page PreparedStore PruneChanges Pruned PutRow PutRows
#   QUERY_CANCELED_SQLSTATE RECORDS_SIGNAL_SOURCE ROWS_UNIT ReadBatch ReadChangeLog ReadEventByKey
#   ReadEvents ReadExpiredEvents ReadRow ReadSignalSource ReadStreamEnd RecordsSchema
#   RecordsSqlFailed Refused ReleaseWriters Reset RetireArchivedEvents RetiredEvents
#   Row RowChanged RowExpiry RowRemoved RowSteps RowsConflict RowsRefused SchemaStep
#   StagedRow Statement StoreHead StoreUnreachable StreamDecl StreamEmpty StreamEnd StreamTail
#   StreamTailEmpty SweepExpired Swept TableDecl TailRead UNBOUNDED_SEQ Unreachable
#   WRITE_KEY_MEMORY_SECONDS WatchChanges WatchCursor WatchEvents Written WrittenRows
#   advance_epoch_locked advance_epoch_statement analyze_statement analyzed append_change_statement
#   append_events_locked append_locked archived_decl archived_stream_names batch_rows
#   batched_writing body_digest built_gaps canonical_json change_log change_log_statement changes_of
#   changes_statement checked_prefix columns_statement committed concurrent_index_statement
#   declared_indexes decoded_batch decoded_value delete_events_statement delete_row_statement
#   departed_keys_statements drop_index_statement drop_keys_statement drop_records_tables
#   drop_statements dropped_bell earlier_use epoch_ms event_by_key_statement event_expiry event_of
#   events_of expire_chosen_events_statement expired_page_statement expired_rows
#   expiring_candidates_statement expiring_sequences find_event_statement find_retired_key_statement
#   find_write_key_statement forget_changes_statement forget_expired_keys forget_keys_statement
#   forget_stream_keys forget_write_keys forget_write_keys_statement guarded_round head_of
#   holder_awaited hung_bell hyx_retired_indexXquestion_markX hyx_retirement_readyXquestion_markX
#   hyx_row_expiredXquestion_markX hyx_step_wantedXquestion_markX
#   hyx_table_shaped_indexXquestion_markX hyx_usable_indexXquestion_markX in_transaction index_gaps
#   index_lock_statement index_lock_taken index_observed index_round index_turn
#   index_unlock_statement index_unlocked indexes_statement insert_event_statement joined_steps
#   judge_append judge_expect judge_put judge_put_rows judge_write_replay key_from_text
#   key_head_refusal key_memory_ms key_text limited_rows list_rows_statement listed_row
#   listed_rows_of lock_observations lock_row_statement locked_round locked_row locked_transaction
#   migrate_lock_key moved_after moved_of next_watch_sequence notice_channel notice_raised
#   notice_topics numbered_statements numbering_lock_statement observed_indexes off_loop
#   pg_answer_read_events pg_list_rows pg_read_event_by_key pg_read_events pg_read_expired_events
#   pg_read_row pg_read_stream_end pg_records_handler pg_retire_archived_events pg_watch_scan
#   prepare_records_store prepare_steps prepared_tables prune_batch prune_bound
#   prune_bound_statement prune_changes_statement pruned_changes purge_expired put_row_locked
#   put_rows_locked query_rows reached read_events_statement read_row_statement reconcile_indexes
#   relations_statement remember_statements remember_write_statement remembered_answer_json
#   remembered_write_of remove_expired_rows removed_row_statements retention_cutoff_ms
#   retire_expired_events retire_keys_statement retire_stream retired_dropped
#   retired_events_statements retired_keys row_expiry row_of run_statements schema_steps signalled
#   signalled_ahead staged_row statement_limit_statement store_head store_head_statement
#   stream_end_statement stream_unit swept_count table_expiries table_unit table_units tail_reads
#   tails_of terminal_rows_statement top_rows_statement touched_events_statement touched_reads
#   unbuilt_fields unit_lock_key upsert_row_statement wait_for_signal watch_head_statement
#   where_refusal write_digest write_key_lock_statement write_key_reads write_parts writer_gates
#   writer_lock_key writer_lock_statement writer_locks writing written_of
"""Moved: ``doeff_records.pg`` is now ``doeff_records._pg`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_records.pg…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_records._pg", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_records._pg")
