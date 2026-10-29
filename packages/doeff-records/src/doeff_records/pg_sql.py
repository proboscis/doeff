# moved-submodule-shim: doeff_records.pg_sql -> doeff_records._pg_sql
# moved-submodule-shim-names doeff_records.pg_sql:
#   AddedColumn ByKeySuffix Clause DEFAULT_PREFIX DeclaredIndex EventExpiry FIELD_NAME_PATTERN
#   HeadRead INDEXED_RELATIONS INDEX_LOCK_SUFFIX InList IndexedField KeepFor ListCursor
#   MIGRATE_LOCK_SUFFIX MODULE_TAGS NO_CLAUSE PREFIX_PATTERN RETIRED_INDEX_PATTERN ROWS_UNIT
#   RecordsSchema RetiredKey RowExpiry RowFilter SEPARATOR_PATTERN STORE_RELATIONS
#   STREAM_UNIT_PREFIX SchemaStep Statement TABLE_NAME_PATTERN TABLE_SHAPED_INDEX_PATTERN
#   TABLE_UNIT_PREFIX TailRead UNBOUNDED_SEQ UNIT_LOCK_SEPARATOR WRITER_LOCK_SUFFIX
#   WRITE_KEY_UNIT_PREFIX added_indexes advance_epoch_statement analyze_statement any_of_condition
#   append_change_statement change_log_statement changes_statement checked_prefix columns_statement
#   concurrent_index_statement declared_indexes delete_events_statement delete_row_statement
#   digest_of drop_index_statement drop_keys_statement drop_statements event_by_key_statement
#   expire_chosen_events_statement expired_event_condition expired_page_statement expired_row_clause
#   expired_row_part expiring_candidates_statement expiring_params expiring_where field_expression
#   field_in_condition field_index field_term find_event_statement find_retired_key_statement
#   find_write_key_statement fixed_index forget_changes_statement forget_keys_statement
#   forget_write_keys_statement former_field_index_name group_index head_index hidden_changes_filter
#   hyx_retired_indexXquestion_markX hyx_scoped_conditionXquestion_markX
#   hyx_table_shaped_indexXquestion_markX in_list index_lock_key index_lock_statement index_name
#   index_steps index_unlock_statement indexes_statement insert_event_statement key_head_expression
#   key_spellings key_suffix_expression key_suffix_of key_text list_rows_statement
#   living_events_filter lock_row_statement migrate_lock_key numbering_lock_statement
#   ordered_expression ordered_index ordered_index_name ordered_within_index
#   ordered_within_index_name params_of prune_bound_statement prune_changes_statement
#   read_events_statement read_row_statement relation_names relations_statement
#   remember_write_statement remembered_at_step retire_keys_statement row_filter schema_statements
#   schema_steps statement_limit_statement store_head_statement stream_end_statement
#   stream_tail_statement stream_unit table_field_expression table_field_index_name table_index_name
#   table_step table_unit terminal_rows_statement terminal_state_condition terminal_words
#   top_rows_statement touched_events_statement unexpired_rows_filter unit_lock_key
#   upsert_row_statement watch_head_statement where_condition write_key_lock_statement
#   write_key_unit writer_lock_key writer_lock_statement
"""Moved: ``doeff_records.pg_sql`` is now ``doeff_records._pg_sql`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_records.pg_sql…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_records._pg_sql import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_records._pg_sql", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_records._pg_sql")
