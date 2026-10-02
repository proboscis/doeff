# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = admission.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass
from datetime import datetime as datetime
from datetime import timezone as timezone
from collections.abc import Mapping as Mapping
from doeff_hy.frozen import FrozenMap as FrozenMap
from doeff_hy.frozen import frozen_json_object as frozen_json_object
from doeff_hy.frozen import thaw_json as thaw_json
from doeff_records.values import RecordsSchema as RecordsSchema
from doeff_records.values import TableDecl as TableDecl
from doeff_records.values import StreamDecl as StreamDecl
from doeff_records.values import KeepFor as KeepFor
from doeff_records.values import ByKeySuffix as ByKeySuffix
from doeff_records.values import Row as Row
from doeff_records.values import Missing as Missing
from doeff_records.values import Conflict as Conflict
from doeff_records.values import Refused as Refused
from doeff_records.values import NotIndexed as NotIndexed
from doeff_records.values import Event as Event
from doeff_records.values import RetiredKey as RetiredKey
from doeff_records.values import ExpectAbsent as ExpectAbsent
from doeff_records.values import ExpectVersion as ExpectVersion
from doeff_records.values import ExpectAny as ExpectAny
from doeff_records.values import RowsConflict as RowsConflict
from doeff_records.values import RowsRefused as RowsRefused

def canonical_json(value: object) -> str:
    ...

def json_bytes(value: object) -> int:
    ...

def hyx_json_equalXquestion_markX(a: object, b: object) -> bool:
    ...

def key_text(key: tuple) -> str:
    ...

def key_from_text(text: str) -> tuple:
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

def hyx_row_expiredXquestion_markX(decl: TableDecl, value: FrozenMap, updated_ms: int, now_ms: int) -> bool:
    ...

def hyx_event_expiredXquestion_markX(decl: StreamDecl, at_ms: int, now_ms: int) -> bool:
    ...

def retention_group_of(decl: StreamDecl, idempotency_key: str) -> str | None:
    ...

def where_refusal(decl: TableDecl, where: FrozenMap) -> NotIndexed | None:
    ...

def hyx_row_matchesXquestion_markX(where: FrozenMap, value: FrozenMap) -> bool:
    ...

def projected(decl: TableDecl, fields: tuple | None, value: FrozenMap) -> FrozenMap:
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
