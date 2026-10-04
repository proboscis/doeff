# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = http_client.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from datetime import datetime as datetime
from doeff import with_handlers as with_handlers
from doeff_core_effects.http_effects import HttpRequest as HttpRequest
from doeff_core_effects.http_effects import HttpResponse as HttpResponse
from doeff_core_effects.http_effects import HttpFailed as HttpFailed
from doeff_core_effects.meter_effects import CountMetric as CountMetric
from doeff_time import GetMonotonic as GetMonotonic
from doeff_time import GetTime as GetTime
from doeff_records.event_source import RECORDS_SIGNAL_SOURCE as RECORDS_SIGNAL_SOURCE
from doeff_records.event_source import ReadSignalSource as ReadSignalSource
from doeff_records.event_source import SignalSourcePatience as SignalSourcePatience
from doeff_records.event_source import came_back_within as came_back_within
from doeff_records.event_source import first_seen as first_seen
from doeff_records.values import Changes as Changes
from doeff_records.values import EventsMoved as EventsMoved
from doeff_records.values import EventsQuiet as EventsQuiet
from doeff_records.values import Reset as Reset
from doeff_records.values import Unreachable as Unreachable
from doeff_records.effects import ReadRow as ReadRow
from doeff_records.effects import ListRows as ListRows
from doeff_records.effects import PutRow as PutRow
from doeff_records.effects import PutRows as PutRows
from doeff_records.effects import WatchChanges as WatchChanges
from doeff_records.effects import WatchEvents as WatchEvents
from doeff_records.effects import AppendEvent as AppendEvent
from doeff_records.effects import ReadEvents as ReadEvents
from doeff_records.effects import ReadStreamEnd as ReadStreamEnd
from doeff_records.effects import ReadSourcePatience as ReadSourcePatience
from doeff_records.wire import PATH_PREFIX as PATH_PREFIX
from doeff_records.wire import PublicEffect as PublicEffect
from doeff_records.wire import WireAnswer as WireAnswer
from doeff_records.wire import JsonValue as JsonValue
from doeff_records.wire import encode_request as encode_request
from doeff_records.wire import decode_answer as decode_answer
from doeff_records.wire import refusal_from as refusal_from
from doeff_records.wire import undeclared_refusal as undeclared_refusal
from doeff_records.wire import CLIENT_ANSWER_METRICS as CLIENT_ANSWER_METRICS
from doeff_records.wire import CLIENT_UNREACHABLE as CLIENT_UNREACHABLE
from doeff_records.wire import client_answer_metric as client_answer_metric
from doeff_records.wire import client_status_outcome as client_status_outcome
from doeff_records.wire import WRITER_HEADER as WRITER_HEADER
from doeff_records.wire import WATCH_MAX_SECONDS as WATCH_MAX_SECONDS
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
DEFAULT_REQUEST_TIMEOUT: float

class WireError(RuntimeError):
    ...

class RecordsUnauthorized(Exception):
    ...
IDENTITY_REFUSED_STATUSES: tuple[int, ...]
REASON_MAX_CHARS: int

@dataclass(frozen=True)
class RecordsEndpoint:
    base_url: str
    request_timeout: float = ...
    meter: Callable[..., object] | None = None
    writer: str | None = None

@dataclass(frozen=True)
class RawReply:
    status: int
    payload: bytes

def service_url(endpoint: RecordsEndpoint, operation: str) -> _Program[str, object]:
    ...

def request_headers(endpoint: RecordsEndpoint) -> _Program[dict[str, str], object]:
    ...

def request_bytes(body: dict[str, object]) -> _Program[bytes, object]:
    ...

def reply_json(operation: str, reply: RawReply) -> _Program[JsonValue, object]:
    ...

def exchange_by_effect(endpoint: RecordsEndpoint, operation: str, body: dict[str, object], waited: float) -> _Program[RawReply | Unreachable, object]:
    ...

def exchange(endpoint: RecordsEndpoint, operation: str, body: dict[str, object], waited: float) -> _Program[RawReply | Unreachable, object]:
    ...

def waited_seconds(ask: PublicEffect) -> _Program[float, object]:
    ...

def refused_reason(payload: bytes) -> _Program[str, object]:
    ...

def identity_refused(endpoint: RecordsEndpoint, operation: str, reply: RawReply) -> _Program[RecordsUnauthorized, object]:
    ...

def zero_client_metrics(endpoint: RecordsEndpoint) -> _Program[None, object]:
    ...

def counted_reply(endpoint: RecordsEndpoint, operation: str, reply: RawReply | Unreachable) -> _Program[None, object]:
    ...

def call_service(endpoint: RecordsEndpoint, ask: PublicEffect) -> _Program[WireAnswer | Unreachable, object]:
    ...

def stall_names(ask: ReadRow | ListRows | PutRow | PutRows | AppendEvent | ReadEvents | ReadStreamEnd) -> _Program[tuple[str, ...], object]:
    ...

def answered_riding_stall(endpoint: RecordsEndpoint, ask: ReadRow | ListRows | PutRow | PutRows | AppendEvent | ReadEvents | ReadStreamEnd) -> _Program[WireAnswer | Unreachable, object]:
    ...

def long_poll(endpoint: RecordsEndpoint, ask: WatchChanges | WatchEvents) -> _Program[Changes | Reset | EventsMoved | EventsQuiet | Unreachable, object]:
    ...

def http_records_handler(endpoint: RecordsEndpoint) -> _Handler:
    ...

def http_table_records_handler(endpoint: RecordsEndpoint, served: frozenset[str]) -> _Handler:
    ...
