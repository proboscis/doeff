# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _laws.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from datetime import datetime as datetime
from collections.abc import Callable as Callable
from doeff import EffectBase as EffectBase
from doeff import Program as Program
from doeff_hy._frozen import FrozenMap as FrozenMap
from doeff_core_effects import Spawn as Spawn
from doeff_core_effects import Wait as Wait
from doeff_time import Delay as Delay
from doeff_time import GetTime as GetTime
from doeff_records._values import FieldDecl as FieldDecl
from doeff_records._values import TableDecl as TableDecl
from doeff_records._values import DescendingWithin as DescendingWithin
from doeff_records._values import StreamDecl as StreamDecl
from doeff_records._values import RecordsSchema as RecordsSchema
from doeff_records._values import KeepFor as KeepFor
from doeff_records._values import KeepForever as KeepForever
from doeff_records._values import ByKeySuffix as ByKeySuffix
from doeff_records._values import ByKeyHead as ByKeyHead
from doeff_records._values import ExpectAbsent as ExpectAbsent
from doeff_records._values import ExpectVersion as ExpectVersion
from doeff_records._values import ExpectAny as ExpectAny
from doeff_records._values import WatchCursor as WatchCursor
from doeff_records._values import ListCursor as ListCursor
from doeff_records._values import Row as Row
from doeff_records._values import Missing as Missing
from doeff_records._values import Page as Page
from doeff_records._values import Written as Written
from doeff_records._values import WrittenRows as WrittenRows
from doeff_records._values import Conflict as Conflict
from doeff_records._values import Refused as Refused
from doeff_records._values import NotIndexed as NotIndexed
from doeff_records._values import Reset as Reset
from doeff_records._values import Changes as Changes
from doeff_records._values import RowChanged as RowChanged
from doeff_records._values import RowRemoved as RowRemoved
from doeff_records._values import Appended as Appended
from doeff_records._values import AppendedEvents as AppendedEvents
from doeff_records._values import Events as Events
from doeff_records._values import EventsMoved as EventsMoved
from doeff_records._values import EventsQuiet as EventsQuiet
from doeff_records._values import RowsConflict as RowsConflict
from doeff_records._values import RowsRefused as RowsRefused
from doeff_records._values import StreamEnd as StreamEnd
from doeff_records._values import StreamEmpty as StreamEmpty
from doeff_records._values import StreamTail as StreamTail
from doeff_records._values import StreamTailEmpty as StreamTailEmpty
from doeff_records._values import Event as Event
from doeff_records._values import EventAbsent as EventAbsent
from doeff_records._values import EventRetired as EventRetired
from doeff_records._values import Unreachable as Unreachable
from doeff_records._values import RememberKeysFor as RememberKeysFor
from doeff_records._values import RememberNoKeys as RememberNoKeys
from doeff_records._values import WRITE_KEY_MEMORY_SECONDS as WRITE_KEY_MEMORY_SECONDS
from doeff_records._values import BatchRead as BatchRead
from doeff_records._values import Count as Count
from doeff_records._effects import ReadRow as ReadRow
from doeff_records._effects import ListRows as ListRows
from doeff_records._effects import PutRow as PutRow
from doeff_records._effects import PutRows as PutRows
from doeff_records._effects import RowWrite as RowWrite
from doeff_records._effects import WatchChanges as WatchChanges
from doeff_records._effects import WatchEvents as WatchEvents
from doeff_records._effects import AppendEvent as AppendEvent
from doeff_records._effects import AppendEvents as AppendEvents
from doeff_records._effects import EventAppend as EventAppend
from doeff_records._effects import ReadEvents as ReadEvents
from doeff_records._effects import ReadStreamEnd as ReadStreamEnd
from doeff_records._effects import ReadEventByKey as ReadEventByKey
from doeff_records._effects import ReadBatch as ReadBatch
from doeff_records._effects import CountRows as CountRows
from doeff_records._faults import AdvanceStoreEpoch as AdvanceStoreEpoch
from doeff_records.maintenance import SweepExpired as SweepExpired
from doeff_records.maintenance import PruneChanges as PruneChanges
from doeff_records.maintenance import ReadChangeLog as ReadChangeLog
from doeff_records.maintenance import ChangeLog as ChangeLog
from doeff_records.maintenance import Swept as Swept
from doeff_records.maintenance import Pruned as Pruned
from doeff_records.maintenance import ReadExpiredEvents as ReadExpiredEvents
from doeff_records.maintenance import ExpiredEvents as ExpiredEvents
from doeff_records.maintenance import RetireArchivedEvents as RetireArchivedEvents
from doeff_records.maintenance import RetiredEvents as RetiredEvents
from doeff_records.maintenance import EventsArchived as EventsArchived
from doeff_records.maintenance import archive_expired_events as archive_expired_events
from functools import partial as partial
from doeff_records.admission import hyx_row_matchesXquestion_markX as hyx_row_matchesXquestion_markX
from doeff_records.admission import epoch_ms as epoch_ms
from doeff_records.admission import key_text as key_text
MAKER: str
PAINTER: str
CLOSER: str
STRANGER: str
OVERSEER: str
TICKET_KEEP_SECONDS: int
PAIR_KEEP_SECONDS: int
PULSE_KEEP_SECONDS: int
RELAY_KEEP_SECONDS: int
BEAT_KEEP_SECONDS: int
BEAT_MEMORY_SECONDS: int
LAW_SCHEMA: RecordsSchema

class LawBroken(AssertionError):
    ...

@dataclass(frozen=True)
class LawHarness:
    as_writer: Callable

def require_law(holds: bool, law: str, detail: str) -> _Program[None, object]:
    ...

def replay_of(again: Appended | Refused | Unreachable, first: Appended | Refused | Unreachable) -> _Program[bool, object]:
    ...

def unplaced(answer: Written | Conflict | Refused | Unreachable | WrittenRows | RowsConflict | RowsRefused) -> _Program[Written | Conflict | Refused | Unreachable | WrittenRows | RowsConflict | RowsRefused, object]:
    ...

def unplaced_all(answers: list) -> _Program[list, object]:
    ...

def as_writer[T](harness: LawHarness, writer: str, program: Program[T, object] | EffectBase[T]) -> _Program[T, object]:
    ...

def collect_changes(harness: LawHarness, tables: tuple, cursor: WatchCursor, limit: int) -> _Program[tuple, object]:
    ...

def collect_pages(harness: LawHarness, table: str, where: FrozenMap, limit: int) -> _Program[list[object], object]:
    ...

def law_stale_put_conflicts(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_committed_changes_appear_once_in_order(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_epoch_change_resets(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_undeclared_writes_are_refused(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_transient_rows_expire(harness: LawHarness) -> _Program[list[object], object]:
    ...
COLORS: tuple[str, ...]
LABELS: tuple[str, ...]
WHERES: list[FrozenMap]

def law_indexed_list_equals_filtered_scan(harness: LawHarness) -> _Program[list[object], object]:
    ...
TOP_NOTES: dict[int, int | str | float | bool | None]
TOP_WHERES: list[FrozenMap]

def law_descending_list_is_the_head_of_the_sorted_scan(harness: LawHarness) -> _Program[list[object], object]:
    ...
DESCENDING_PAGE_CAP: int

def sorted_descending_scan(harness: LawHarness, where: FrozenMap) -> _Program[tuple, object]:
    ...

def collect_descending_pages(harness: LawHarness, where: FrozenMap, fields: tuple | None, cursor: ListCursor | None, limit: int) -> _Program[list[object], object]:
    ...

def law_descending_pages_follow_the_sorted_scan(harness: LawHarness) -> _Program[list[object], object]:
    ...
ANY_OF_ROWS: tuple[FrozenMap, ...]
ANY_OF_CASES: list[tuple[FrozenMap, ...]]
ANY_OF_PAGE: int
ANY_OF_TOP: int

def collect_any_pages(harness: LawHarness, table: str, where: FrozenMap, where_any: FrozenMap, limit: int) -> _Program[list[object], object]:
    ...

def union_of_equal_lists(harness: LawHarness, table: str, where: FrozenMap, where_any: FrozenMap) -> _Program[tuple, object]:
    ...

def law_any_of_list_equals_the_union_of_equal_lists(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_append_is_idempotent(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_append_events_answer_like_appends_in_order(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_read_batch_answers_like_each_read(harness: LawHarness) -> _Program[list[object], object]:
    ...
TRAIL_KEYS: tuple[str, ...]
TRAIL_HEADS: tuple[str, ...]

def law_key_head_read_equals_filtered_scan(harness: LawHarness) -> _Program[list[object], object]:
    ...

def late_write(harness: LawHarness) -> _Program[Written, object]:
    ...

def law_watch_waits_for_a_change(harness: LawHarness) -> _Program[list[object], object]:
    ...

def late_append(harness: LawHarness) -> _Program[Appended, object]:
    ...

def law_watch_events_waits_for_an_append(harness: LawHarness) -> _Program[list[object], object]:
    ...
CONCURRENT_WRITERS: int
CONCURRENT_ROUNDS: int
FOLLOW_READS: int
FOLLOW_LIMIT: int

def concurrent_appender(harness: LawHarness, n: int) -> _Program[None, object]:
    ...

def concurrent_row_writer(harness: LawHarness, n: int) -> _Program[None, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class FollowedEvents:
    sequences: tuple[int, ...]
    after: int

@dataclass(frozen=True, kw_only=True)
class Followed:
    events: tuple[int, ...]
    after: int
    changes: tuple[int, ...]
    at: WatchCursor

def followed_events(harness: LawHarness, after: int) -> _Program[FollowedEvents, object]:
    ...

def concurrent_follower(harness: LawHarness, cursor: WatchCursor) -> _Program[Followed, object]:
    ...

def law_concurrent_writes_appear_in_number_order(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_none_removes_a_field(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_maintenance_prunes_and_sweeps(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_prune_stops_at_the_archived_seq(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_change_log_reads_every_change(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_put_rows_is_all_or_nothing(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_grouped_events_expire_together(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_stream_end_is_the_last_sequence(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_expired_keys_are_remembered(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_expired_records_are_unseen_before_a_sweep(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_a_write_clears_the_expired_row_it_touches(harness: LawHarness) -> _Program[list[object], object]:
    ...

def expired_key_answers(harness: LawHarness) -> _Program[tuple, object]:
    ...

def law_an_expired_key_answers_the_same_before_and_after_a_sweep(harness: LawHarness) -> _Program[list[object], object]:
    ...

def tails_of_last_events(harness: LawHarness, streams: tuple[str, ...]) -> _Program[tuple[StreamTail | StreamTailEmpty, ...], object]:
    ...

def law_watch_tails_match_last_events(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_event_by_key_reads_the_same_event(harness: LawHarness) -> _Program[list[object], object]:
    ...
SHORT_PAGE_LIMIT: int

def short_page_is_the_end(harness: LawHarness, stream: str, page: Events, limit: int, law: str) -> _Program[list[object], object]:
    ...

def law_short_page_ends_the_stream(harness: LawHarness) -> _Program[list[object], object]:
    ...

def expired_keys(harness: LawHarness, at: datetime) -> _Program[tuple, object]:
    ...

def confirm_keys(keys: frozenset, events: tuple) -> _Program[EventsArchived, object]:
    ...

def unreachable_destination(events: tuple) -> _Program[Unreachable, object]:
    ...

def law_only_confirmed_events_are_retired(harness: LawHarness) -> _Program[list[object], object]:
    ...

def confirm_with_a_late_guess(late: int, events: tuple) -> _Program[EventsArchived, object]:
    ...

def law_retirement_keeps_the_read_boundary(harness: LawHarness) -> _Program[list[object], object]:
    ...

def relay_answers(harness: LawHarness) -> _Program[tuple, object]:
    ...

def law_a_write_keeps_the_unarchived_event_it_touches(harness: LawHarness) -> _Program[list[object], object]:
    ...

def hyx_fresh_appendXquestion_markX(answer: Appended | Refused | Unreachable, earlier: Appended | Refused | Unreachable) -> _Program[bool, object]:
    ...

def law_keys_are_forgotten_after_their_memory(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_unarchived_keys_are_kept_until_retired(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_a_write_names_its_change_position(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_a_keyed_write_replays_its_answer(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_another_key_is_judged_by_its_expectation(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_write_keys_are_forgotten_after_their_memory(harness: LawHarness) -> _Program[list[object], object]:
    ...

def ascending_pages(harness: LawHarness, where: FrozenMap, limit: int) -> _Program[list[object], object]:
    ...

def law_ascending_list_and_count_follow_the_scan(harness: LawHarness) -> _Program[list[object], object]:
    ...
LAWS: Incomplete
SHARED_LAWS: tuple[str, ...]
