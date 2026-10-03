# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = service.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from collections.abc import Callable as Callable
from doeff import with_handlers as with_handlers
from doeff_records.values import RecordsSchema as RecordsSchema
from doeff_records.values import Unreachable as Unreachable
from doeff_records.effects import WatchChanges as WatchChanges
from doeff_records.effects import WatchEvents as WatchEvents
from doeff_records.principals import Principal as Principal
from doeff_records.principals import writer_of as writer_of
from doeff_records.wire import PATH_PREFIX as PATH_PREFIX
from doeff_records.wire import OPERATIONS as OPERATIONS
from doeff_records.wire import PublicEffect as PublicEffect
from doeff_records.wire import WireRequest as WireRequest
from doeff_records.wire import WireRefusal as WireRefusal
from doeff_records.wire import WireMalformed as WireMalformed
from doeff_records.wire import STATUS_OF_ERROR as STATUS_OF_ERROR
from doeff_records.wire import ERROR_MALFORMED as ERROR_MALFORMED
from doeff_records.wire import ERROR_NOT_FOUND as ERROR_NOT_FOUND
from doeff_records.wire import ERROR_STORE_UNAVAILABLE as ERROR_STORE_UNAVAILABLE
from doeff_records.wire import WATCH_MAX_SECONDS as WATCH_MAX_SECONDS
from doeff_records.wire import decode_request as decode_request
from doeff_records.wire import encode_answer as encode_answer
from doeff_records.wire import refusal_json as refusal_json
from doeff_records.wire import undeclared_reason as undeclared_reason
METHOD_GET: str
METHOD_POST: str
PATH_HEALTHZ: str

@dataclass(frozen=True)
class HttpRequest:
    method: str
    path: str
    body: bytes
    writer: str | None = None

@dataclass(frozen=True)
class HttpAnswer:
    status: int
    body: str

@dataclass(frozen=True)
class RecordsService:
    schema: RecordsSchema
    handler_for: Callable

def records_service(schema: RecordsSchema, handler_for: Callable) -> _Program[RecordsService, object]:
    ...

def json_answer(status: int, body: dict) -> _Program[HttpAnswer, object]:
    ...

def refusal_answer(error: str, reason: str) -> _Program[HttpAnswer, object]:
    ...

def undeclared_name(schema: RecordsSchema, ask: PublicEffect) -> _Program[str | None, object]:
    ...

def serve_operation(service: RecordsService, principal: Principal, operation: str, body: bytes) -> _Program[HttpAnswer, object]:
    ...

def respond(service: RecordsService, request: HttpRequest) -> _Program[HttpAnswer, object]:
    ...
