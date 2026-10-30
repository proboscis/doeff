# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _admission.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from datetime import datetime as datetime
from datetime import timezone as timezone
from collections.abc import Mapping as Mapping
from doeff_hy._frozen import FrozenMap as FrozenMap
from doeff_hy._frozen import frozen_json_object as frozen_json_object
from doeff_hy._frozen import thaw_json as thaw_json
from doeff_records._values import RecordsSchema as RecordsSchema
from doeff_records._values import TableDecl as TableDecl
from doeff_records._values import StreamDecl as StreamDecl
from doeff_records._values import KeepFor as KeepFor
from doeff_records._values import ByKeySuffix as ByKeySuffix
from doeff_records._values import Row as Row
from doeff_records._values import Missing as Missing
from doeff_records._values import Conflict as Conflict
from doeff_records._values import Refused as Refused
from doeff_records._values import NotIndexed as NotIndexed
from doeff_records._values import Event as Event
from doeff_records._values import RetiredKey as RetiredKey
from doeff_records._values import ExpectAbsent as ExpectAbsent
from doeff_records._values import ExpectVersion as ExpectVersion
from doeff_records._values import ExpectAny as ExpectAny
from doeff_records._values import RowsConflict as RowsConflict
from doeff_records._values import RowsRefused as RowsRefused
from doeff_records._values import Written as Written
from doeff_records._values import WrittenRows as WrittenRows
from doeff_records._values import RememberedWrite as RememberedWrite
from doeff_records._values import WrittenAndAppended as WrittenAndAppended
from doeff_records._values import Appended as Appended

def canonical_json(value: object) -> str:
    ...

def json_bytes(value: object) -> int:
    ...

def hyx_json_equalXquestion_markX(a: object, b: object) -> bool:
    ...

def key_text(key: tuple[str, ...]) -> str:
    ...

def key_from_text(text: str) -> tuple[str, ...]:
    ...

def epoch_ms(at: datetime) -> int:
    ...

def judge_expect(expect: object, current: Row | None) -> Conflict | None:
    ...

@dataclass(frozen=True)
class Admitted:
    value: FrozenMap

def state_of(decl: TableDecl, value: FrozenMap) -> str | None:
    ...

def hyx_terminal_rowXquestion_markX(decl: TableDecl, value: FrozenMap) -> bool:
    ...

def judged_diff(decl: TableDecl, diff: FrozenMap) -> FrozenMap:
    ...

def shape_refusal(decl: TableDecl, current: Row | None, key: tuple, diff: FrozenMap) -> Refused | None:
    ...

def landed_value(decl: TableDecl, current: Row | None, key: tuple, diff: FrozenMap) -> FrozenMap:
    ...

def state_refusal(decl: TableDecl, value: FrozenMap) -> Refused | None:
    ...

def size_refusal(decl: TableDecl, value: FrozenMap) -> Refused | None:
    ...

def judge_put(decl: TableDecl, current: Row | None, key: tuple, diff: FrozenMap) -> Admitted | Refused:
    ...

def judge_put_rows(schema: RecordsSchema, writes: tuple, currents: tuple) -> tuple | RowsConflict | RowsRefused:
    ...

def expect_form(expect: object) -> dict:
    ...

def write_digest(rows: bool, writes: tuple, appends: tuple | None=None) -> str:
    ...

def replayed_answer(answer: Written | WrittenRows | WrittenAndAppended) -> Written | WrittenRows | WrittenAndAppended:
    ...

def judge_write_replay(key: str | None, rows: bool, writes: tuple, remembered: RememberedWrite | None, appends: tuple | None=None) -> Written | WrittenRows | WrittenAndAppended | Refused | RowsRefused | None:
    ...

def retention_cutoff_ms(decl: TableDecl | StreamDecl, now_ms: int) -> int | None:
    ...

def hyx_row_expiredXquestion_markX(decl: TableDecl, value: FrozenMap, updated_ms: int, now_ms: int) -> bool:
    ...

def hyx_event_expiredXquestion_markX(decl: StreamDecl, at_ms: int, now_ms: int) -> bool:
    ...

def retention_group_of(decl: StreamDecl, idempotency_key: str) -> str | None:
    ...

def key_head_of(separator: str, idempotency_key: str) -> str:
    ...

def key_head_refusal(decl: StreamDecl, key_head: str | None) -> NotIndexed | None:
    ...

def where_refusal(decl: TableDecl, where: FrozenMap, where_any: FrozenMap) -> NotIndexed | None:
    ...

def hyx_row_matchesXquestion_markX(where: FrozenMap, value: FrozenMap) -> bool:
    ...

def hyx_row_matches_anyXquestion_markX(where_any: FrozenMap, value: FrozenMap) -> bool:
    ...

def hyx_descending_valueXquestion_markX(value: object) -> bool:
    ...

def projected(decl: TableDecl, fields: tuple, value: FrozenMap) -> FrozenMap:
    ...

def listed_row(decl: TableDecl, fields: tuple | None, row: Row) -> Row:
    ...

def next_watch_sequence(items: tuple, limit: int, head: int) -> int:
    ...

@dataclass(frozen=True)
class AppendNew:
    ...

@dataclass(frozen=True)
class AppendReplay:
    sequence: int

def body_digest(body: object) -> str:
    ...

def judge_append(decl: StreamDecl, body: object, earlier: Event | RetiredKey | None) -> AppendNew | AppendReplay | Refused:
    ...
