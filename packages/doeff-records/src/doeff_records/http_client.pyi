"""http_client.hy の公開面の型(記録の service の client — 型検査のための宣言・実行時は http_client.hy を読む)。

http_client.hy は Hy の module なので、型の宣言が無いと pyright は中を読めず、口の組 RecordsEndpoint・答え手 http-records-handler・
計器の 0 置き zero-client-metrics が Unknown になる(使い手の job の土台が口を作って答え手を並べる所の strict の型検査で、書き手に
直せない赤が連なる)。ここで型を宣言する。

- `(defclass [(dataclass :frozen True)] …)` は位置でも渡せる frozen の dataclass。
- defk は呼ぶと Program を返す(答えの型 = 実装の :post の型)。
- defhandler(http-records-handler・http-table-records-handler)は引数を受け、本文の Program に被せる関数を返す。
- RecordsEndpoint.meter は計器の答え手(CountMetric に答える handler — 形は答え手ごとに違うので、実装の注記と同じ Callable に留める)。
- PublicEffect・WireAnswer は wire.hy の union(wire.hy には型の宣言が無いので、同じ並びを effects.pyi・values.pyi の名で書く)。
- 実装との食い違いは packages/doeff-records/tests/test_static_stubs.py が名・欄の名と順・既定値の有無・引数の名・union の並びで検める。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeAlias, TypeVar

from doeff_hy.json_value import JsonValue
from doeff_records.effects import (
    AppendEvent,
    ListRows,
    PutRow,
    PutRows,
    ReadEvents,
    ReadRow,
    ReadStreamEnd,
    WatchChanges,
)
from doeff_records.values import (
    Appended,
    Changes,
    Conflict,
    Events,
    EventsMoved,
    EventsQuiet,
    Missing,
    NotIndexed,
    Page,
    Refused,
    Reset,
    Row,
    RowsConflict,
    RowsRefused,
    StreamEmpty,
    StreamEnd,
    Unreachable,
    Written,
    WrittenRows,
)
from doeff_vm import WithHandler

from doeff import Program

_A = TypeVar("_A")

PublicEffect: TypeAlias = (
    ReadRow | ListRows | PutRow | WatchChanges | AppendEvent | ReadEvents | PutRows | ReadStreamEnd
)
WireAnswer: TypeAlias = (
    Row
    | Missing
    | Page
    | Written
    | Conflict
    | Refused
    | NotIndexed
    | Reset
    | Changes
    | Appended
    | Events
    | WrittenRows
    | RowsConflict
    | RowsRefused
    | StreamEnd
    | StreamEmpty
)

DEFAULT_REQUEST_TIMEOUT: float
DEFAULT_POLL_SECONDS: float
IDENTITY_REFUSED_STATUSES: tuple[int, ...]
REASON_MAX_CHARS: int

class WireError(RuntimeError): ...
class RecordsUnauthorized(Exception): ...

@dataclass(frozen=True)
class RecordsEndpoint:
    base_url: str
    token: str
    request_timeout: float = ...
    poll_seconds: float = ...
    meter: Callable[..., object] | None = ...

@dataclass(frozen=True)
class RawReply:
    status: int
    payload: bytes

class _RecordsHandler(Protocol):
    """本文の Program に記録の effect の答え手を被せる関数(答えの型は本文のまま)。"""

    def __call__(self, body: Program[_A, object], /) -> WithHandler[_A]: ...

def service_url(endpoint: RecordsEndpoint, operation: str) -> Program[str, object]: ...
def request_headers(endpoint: RecordsEndpoint) -> Program[dict[str, str], object]: ...
def request_bytes(body: dict[str, object]) -> Program[bytes, object]: ...
def reply_json(operation: str, reply: RawReply) -> Program[JsonValue, object]: ...
def exchange_by_effect(
    endpoint: RecordsEndpoint, operation: str, body: dict[str, object]
) -> Program[RawReply | Unreachable, object]: ...
def exchange(endpoint: RecordsEndpoint, operation: str, body: dict[str, object]) -> Program[RawReply | Unreachable, object]: ...
def refused_reason(payload: bytes) -> Program[str, object]: ...
def identity_refused(endpoint: RecordsEndpoint, operation: str, reply: RawReply) -> Program[RecordsUnauthorized, object]: ...
def moved_by_reading(
    endpoint: RecordsEndpoint, ask: ReadEvents
) -> Program[EventsMoved | EventsQuiet | Unreachable, object]: ...
def zero_client_metrics(endpoint: RecordsEndpoint) -> Program[None, object]: ...
def counted_reply(endpoint: RecordsEndpoint, operation: str, reply: RawReply | Unreachable) -> Program[None, object]: ...
def call_service(endpoint: RecordsEndpoint, ask: PublicEffect) -> Program[WireAnswer | Unreachable, object]: ...
def http_records_handler(endpoint: RecordsEndpoint) -> _RecordsHandler: ...
def http_table_records_handler(endpoint: RecordsEndpoint, served: frozenset[str]) -> _RecordsHandler: ...
