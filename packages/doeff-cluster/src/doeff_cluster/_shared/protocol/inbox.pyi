# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = inbox.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from typing import Protocol as Protocol
from typing import runtime_checkable as runtime_checkable
from urllib.parse import unquote as url_unquote
from doeff_core_effects import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects import ExternalPromise as ExternalPromise
from doeff_core_effects import TaskPriority as TaskPriority
from doeff_core_effects import Promise as Promise
from doeff_core_effects import Wait as Wait
from doeff_time import WaitWithin as WaitWithin
from doeff_cluster._shared.intent.protocol import Request as Request
from doeff_cluster._shared.intent.protocol import Reply as Reply
from doeff_cluster._shared.intent.protocol import CoordinatorStopRequested as CoordinatorStopRequested
from doeff_cluster._shared.intent.protocol import PlainText as PlainText
from doeff_cluster._shared.intent.protocol import NextRequests as NextRequests
from doeff_cluster._shared.intent.protocol import ServiceReached as ServiceReached

@runtime_checkable
class ReplyTarget(Protocol):
    done: object = None
    status: int = 0
    data: bytes = b''
    content_type: str = ''

@runtime_checkable
class InboxQueue(Protocol):

    def arm(self, timeout: float | None, bell: ExternalPromise) -> bool:
        ...

    def taken(self, limit: int) -> list:
        ...
    reached: bool = False

class StopSignal(Protocol):
    requested: bool = False

def http_request(method: str, path: str, query: dict[str, object], body: dict[str, object] | list[object] | str | int | float | bool | None, slot: ReplyTarget | Promise[tuple[int, object]] | None=None, actor: str | None=None, peer: str='', queued_ms: int=0) -> _Program[Request, object]:
    ...

def requests_of(raws: list) -> _Program[tuple[Request, ...], object]:
    ...

def encoded_reply(body: object) -> tuple:
    ...

def raw_requests(inbox: InboxQueue, timeout_seconds: float | None, limit: int) -> _Program[list, object]:
    ...

def http_requests(inbox: InboxQueue) -> _Handler:
    ...

def stop_flag(state: StopSignal) -> _Handler:
    ...
