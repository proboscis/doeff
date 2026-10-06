# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = inbox.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from typing import Protocol as Protocol
from typing import runtime_checkable as runtime_checkable
from urllib.parse import unquote as url_unquote
from doeff_core_effects.scheduler import Promise as Promise
from doeff_cluster.shared.intent.protocol import Request as Request
from doeff_cluster.shared.intent.protocol import Reply as Reply
from doeff_cluster.shared.intent.protocol import CoordinatorStopRequested as CoordinatorStopRequested
from doeff_cluster.shared.intent.protocol import PlainText as PlainText
from doeff_cluster.shared.intent.protocol import NextRequests as NextRequests
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler

@runtime_checkable
class ReplyTarget(Protocol):
    done: object = None
    status: int = 0
    data: bytes = b''
    content_type: str = ''

class InboxQueue(Protocol):

    def take(self, timeout: float | None, limit: int) -> list:
        ...

class StopSignal(Protocol):
    requested: bool = False

def http_request(method: str, path: str, query: dict, body: dict | list | str | int | float | bool | None, slot: ReplyTarget | Promise | None=None, actor: str | None=None, peer: str='') -> _Program[Request, object]:
    ...

def requests_of(raws: list) -> _Program[tuple[Request, ...], object]:
    ...

def encoded_reply(body: object) -> tuple:
    ...

def http_requests(inbox: InboxQueue) -> _Handler:
    ...

def stop_flag(state: StopSignal) -> _Handler:
    ...
