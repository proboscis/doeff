# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = http_client.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from datetime import datetime as datetime
from doeff import with_handlers as with_handlers
from doeff_core_effects.effects import slog as slog
from doeff_core_effects.http_effects import HttpRequest as HttpRequest
from doeff_core_effects.http_effects import HttpResponse as HttpResponse
from doeff_core_effects.http_effects import HttpFailed as HttpFailed
from doeff_core_effects.http_effects import HttpPhases as HttpPhases
from doeff_core_effects.meter_effects import CountMetric as CountMetric
from doeff_time import GetMonotonic as GetMonotonic
from doeff_time import GetTime as GetTime
from doeff_records.event_source import RECORDS_SIGNAL_SOURCE as RECORDS_SIGNAL_SOURCE
from doeff_records.event_source import ReadSignalSource as ReadSignalSource
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
from doeff_records.effects import AppendEvents as AppendEvents
from doeff_records.effects import ReadEvents as ReadEvents
from doeff_records.effects import ReadStreamEnd as ReadStreamEnd
from doeff_records.effects import ReadEventByKey as ReadEventByKey
from doeff_records.effects import ReadRequestPatience as ReadRequestPatience
from doeff_records.wire import PATH_PREFIX as PATH_PREFIX
from doeff_records.wire import PublicEffect as PublicEffect
from doeff_records.wire import WireAnswer as WireAnswer
from doeff_records.wire import JsonValue as JsonValue
from doeff_records.wire import encode_request as encode_request
from doeff_records.wire import decode_answer as decode_answer
from doeff_records.wire import answer_for_request as answer_for_request
from doeff_records.wire import refusal_from as refusal_from
from doeff_records.wire import undeclared_refusal as undeclared_refusal
from doeff_records.wire import CLIENT_ANSWER_METRICS as CLIENT_ANSWER_METRICS
from doeff_records.wire import CLIENT_UNREACHABLE as CLIENT_UNREACHABLE
from doeff_records.wire import client_answer_metric as client_answer_metric
from doeff_records.wire import client_status_outcome as client_status_outcome
from doeff_records.wire import WRITER_HEADER as WRITER_HEADER
from doeff_records.wire import CALLER_HEADER as CALLER_HEADER
from doeff_records.wire import WATCH_MAX_SECONDS as WATCH_MAX_SECONDS
from doeff_records.wire import SERVER_TIMING_HEADER as SERVER_TIMING_HEADER
from doeff_records.wire import SERVER_TIMING_QUEUE as SERVER_TIMING_QUEUE
from doeff_records.wire import server_timing_spans as server_timing_spans
from doeff_records.wire import IDEMPOTENCY_HEADER as IDEMPOTENCY_HEADER
from doeff_records.wire import request_key as request_key
from doeff_core_effects.random_effects import RandomBytes as RandomBytes
from doeff_core_effects.os_random import os_random_handler as os_random_handler
DEFAULT_REQUEST_TIMEOUT: float
EXCHANGE_LOG: str

class WireError(RuntimeError):
    ...

@dataclass(frozen=True)
class RecordsEndpoint:
    base_url: str
    request_timeout: float = ...
    meter: Callable[..., object] | None = None
    writer: str | None = None
    caller: str | None = None
    timed_operations: frozenset[str] = ...

@dataclass(frozen=True)
class RawReply:
    status: int
    payload: bytes
    phases: HttpPhases | None = None
    server_timing: str | None = None

def service_url(endpoint: RecordsEndpoint, operation: str) -> _Program[str, object]:
    ...

def request_headers(endpoint: RecordsEndpoint, key: str | None) -> _Program[dict[str, str], object]:
    ...

def request_bytes(body: dict[str, object]) -> _Program[bytes, object]:
    ...

def reply_json(operation: str, reply: RawReply) -> _Program[JsonValue, object]:
    ...

def exchange_by_effect(endpoint: RecordsEndpoint, operation: str, body: dict[str, object], waited: float, key: str | None) -> _Program[RawReply | Unreachable, object]:
    ...

def exchange(endpoint: RecordsEndpoint, operation: str, body: dict[str, object], waited: float, key: str | None) -> _Program[RawReply | Unreachable, object]:
    ...

def waited_seconds(ask: PublicEffect) -> _Program[float, object]:
    ...

def zero_client_metrics(endpoint: RecordsEndpoint) -> _Program[None, object]:
    ...

def counted_reply(endpoint: RecordsEndpoint, operation: str, reply: RawReply | Unreachable) -> _Program[None, object]:
    ...

def ms_of(seconds: float) -> _Program[float, object]:
    ...

def tables_named(ask: PublicEffect) -> _Program[str, object]:
    ...

def logged_exchange(ask: PublicEffect, operation: str, reply: RawReply | Unreachable, asked: float, answered: float, decoded: float) -> _Program[None, object]:
    ...

def reply_answer(endpoint: RecordsEndpoint, ask: PublicEffect, operation: str, reply: RawReply | Unreachable) -> _Program[WireAnswer | Unreachable, object]:
    ...

def call_service(endpoint: RecordsEndpoint, ask: PublicEffect) -> _Program[WireAnswer | Unreachable, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class RequestPatience:
    seconds: float

    def __post_init__(self) -> None:
        ...

def request_patience_handler(patience: RequestPatience) -> _Handler:
    ...
records_unwaited: _Handler

def stall_names(ask: ReadRow | ListRows | PutRow | PutRows | AppendEvent | AppendEvents | ReadEvents | ReadStreamEnd | ReadEventByKey) -> _Program[tuple[str, ...], object]:
    ...
WRITE_KEY_BYTES: int

def fresh_write_key(ask: ReadRow | ListRows | PutRow | PutRows | AppendEvent | AppendEvents | ReadEvents | ReadStreamEnd | ReadEventByKey) -> _Program[str | None, object]:
    ...

def answered_riding_stall(endpoint: RecordsEndpoint, ask: ReadRow | ListRows | PutRow | PutRows | AppendEvent | AppendEvents | ReadEvents | ReadStreamEnd | ReadEventByKey) -> _Program[WireAnswer | Unreachable, object]:
    ...

def long_poll(endpoint: RecordsEndpoint, ask: WatchChanges | WatchEvents) -> _Program[Changes | Reset | EventsMoved | EventsQuiet | Unreachable, object]:
    ...

def http_records_handler(endpoint: RecordsEndpoint) -> _Handler:
    ...

def http_table_records_handler(endpoint: RecordsEndpoint, served: frozenset[str]) -> _Handler:
    ...
