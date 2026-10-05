# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = laws.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from collections.abc import Callable as Callable
from doeff import EffectBase as EffectBase
from doeff import Program as Program
from doeff_hy.frozen import FrozenMap as FrozenMap
from doeff_core_effects.scheduler import Spawn as Spawn
from doeff_core_effects.scheduler import Wait as Wait
from doeff_time import Delay as Delay
from doeff_time import GetTime as GetTime
from doeff_records.values import FieldDecl as FieldDecl
from doeff_records.values import TableDecl as TableDecl
from doeff_records.values import StreamDecl as StreamDecl
from doeff_records.values import RecordsSchema as RecordsSchema
from doeff_records.values import KeepFor as KeepFor
from doeff_records.values import KeepForever as KeepForever
from doeff_records.values import ByKeySuffix as ByKeySuffix
from doeff_records.values import ExpectAbsent as ExpectAbsent
from doeff_records.values import ExpectVersion as ExpectVersion
from doeff_records.values import ExpectAny as ExpectAny
from doeff_records.values import WatchCursor as WatchCursor
from doeff_records.values import ListCursor as ListCursor
from doeff_records.values import Row as Row
from doeff_records.values import Missing as Missing
from doeff_records.values import Page as Page
from doeff_records.values import Written as Written
from doeff_records.values import WrittenRows as WrittenRows
from doeff_records.values import Conflict as Conflict
from doeff_records.values import Refused as Refused
from doeff_records.values import NotIndexed as NotIndexed
from doeff_records.values import Reset as Reset
from doeff_records.values import Changes as Changes
from doeff_records.values import RowChanged as RowChanged
from doeff_records.values import RowRemoved as RowRemoved
from doeff_records.values import Appended as Appended
from doeff_records.values import Events as Events
from doeff_records.values import EventsMoved as EventsMoved
from doeff_records.values import EventsQuiet as EventsQuiet
from doeff_records.values import RowsConflict as RowsConflict
from doeff_records.values import RowsRefused as RowsRefused
from doeff_records.values import StreamEnd as StreamEnd
from doeff_records.values import StreamEmpty as StreamEmpty
from doeff_records.effects import ReadRow as ReadRow
from doeff_records.effects import ListRows as ListRows
from doeff_records.effects import PutRow as PutRow
from doeff_records.effects import PutRows as PutRows
from doeff_records.effects import RowWrite as RowWrite
from doeff_records.effects import WatchChanges as WatchChanges
from doeff_records.effects import WatchEvents as WatchEvents
from doeff_records.effects import AppendEvent as AppendEvent
from doeff_records.effects import ReadEvents as ReadEvents
from doeff_records.effects import ReadStreamEnd as ReadStreamEnd
from doeff_records.faults import AdvanceStoreEpoch as AdvanceStoreEpoch
from doeff_records.maintenance import SweepExpired as SweepExpired
from doeff_records.maintenance import PruneChanges as PruneChanges
from doeff_records.maintenance import Swept as Swept
from doeff_records.maintenance import Pruned as Pruned
from doeff_records.admission import hyx_row_matchesXquestion_markX as hyx_row_matchesXquestion_markX
from doeff_records.admission import epoch_ms as epoch_ms
MAKER: str
PAINTER: str
CLOSER: str
STRANGER: str
OVERSEER: str
TICKET_KEEP_SECONDS: int
PAIR_KEEP_SECONDS: int
PULSE_KEEP_SECONDS: int
LAW_SCHEMA: RecordsSchema

class LawBroken(AssertionError):
    ...

@dataclass(frozen=True)
class LawHarness:
    as_writer: Callable

def require_law(holds: bool, law: str, detail: str) -> _Program[None, object]:
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

def law_append_is_idempotent(harness: LawHarness) -> _Program[list[object], object]:
    ...

def late_write(harness: LawHarness) -> _Program[Written, object]:
    ...

def law_watch_waits_for_a_change(harness: LawHarness) -> _Program[list[object], object]:
    ...

def late_append(harness: LawHarness) -> _Program[Appended, object]:
    ...

def law_watch_events_waits_for_an_append(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_none_removes_a_field(harness: LawHarness) -> _Program[list[object], object]:
    ...

def law_maintenance_prunes_and_sweeps(harness: LawHarness) -> _Program[list[object], object]:
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
LAWS: Incomplete
SHARED_LAWS: tuple[str, ...]
