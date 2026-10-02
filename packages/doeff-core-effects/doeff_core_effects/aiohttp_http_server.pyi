# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = aiohttp_http_server.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff_hy.static_types import Handler as _Handler
import asyncio as asyncio
from collections import deque as deque
from collections.abc import Coroutine as Coroutine
import sys as sys
import threading as threading
import time as time
from typing import TypeVar as TypeVar
from pathlib import Path as Path
import aiohttp as aiohttp
from aiohttp import web as web
from aiohttp import WSMsgType as WSMsgType
from doeff_core_effects.effects import Await as Await
from doeff_core_effects.http_server_effects import HttpAddress as HttpAddress
from doeff_core_effects.http_server_effects import HttpServerClosed as HttpServerClosed
from doeff_core_effects.http_server_effects import HttpCommand as HttpCommand
from doeff_core_effects.http_server_effects import HttpEvent as HttpEvent
from doeff_core_effects.http_server_effects import HttpHeader as HttpHeader
from doeff_core_effects.http_server_effects import HttpRequestArrived as HttpRequestArrived
from doeff_core_effects.http_server_effects import HttpListen as HttpListen
from doeff_core_effects.http_server_effects import HttpNextRequest as HttpNextRequest
from doeff_core_effects.http_server_effects import HttpRespond as HttpRespond
from doeff_core_effects.http_server_effects import HttpForward as HttpForward
from doeff_core_effects.http_server_effects import WsForward as WsForward
from doeff_core_effects.http_server_effects import WsAccept as WsAccept
from doeff_core_effects.http_server_effects import WsSendText as WsSendText
from doeff_core_effects.http_server_effects import WsClose as WsClose
from doeff_core_effects.http_server_effects import HttpShutdown as HttpShutdown
from doeff_core_effects.http_server_effects import TakeWsSendReport as TakeWsSendReport
from doeff_core_effects.http_server_effects import WsSendReport as WsSendReport
from doeff_core_effects.http_server_effects import WsOpened as WsOpened
from doeff_core_effects.http_server_effects import WsTextArrived as WsTextArrived
from doeff_core_effects.http_server_effects import WsBinaryArrived as WsBinaryArrived
from doeff_core_effects.http_server_effects import WsClosed as WsClosed
from doeff_core_effects.http_server_effects import HttpBodyBytes as HttpBodyBytes
from doeff_core_effects.http_server_effects import HttpBodyFileRange as HttpBodyFileRange
from doeff_core_effects.http_server_effects import HttpNoBody as HttpNoBody
from doeff_core_effects.http_server_effects import FLUSH_SAMPLES_LIMIT as FLUSH_SAMPLES_LIMIT
from doeff_core_effects.http_server_effects import DEFAULT_DRAIN_SECONDS as DEFAULT_DRAIN_SECONDS
from doeff_core_effects.http_server_effects import WS_CLOSE_NORMAL as WS_CLOSE_NORMAL
from doeff_core_effects.http_server_effects import WS_CLOSE_ABNORMAL as WS_CLOSE_ABNORMAL
from doeff_core_effects.http_server_effects import HttpReadBody as HttpReadBody
from doeff_core_effects.http_server_effects import HttpBodyRead as HttpBodyRead
from doeff_core_effects.http_server_effects import HttpBodyTooLarge as HttpBodyTooLarge
from doeff_core_effects.http_server_effects import HttpBodyFailed as HttpBodyFailed
from doeff_core_effects.http_server_effects import HttpBodyOutcome as HttpBodyOutcome
from doeff_core_effects.http_server_effects import WS_REFUSAL_TEXT as WS_REFUSAL_TEXT
from doeff_core_effects.http_server_effects import WS_CUT_REASON as WS_CUT_REASON
from doeff_core_effects.http_server_effects import WsCloseFrame as WsCloseFrame
from doeff_core_effects.http_server_effects import carries_content as carries_content
from doeff_core_effects.http_server_effects import ws_refusal_status as ws_refusal_status
from doeff_core_effects.http_server_effects import send_overflows as send_overflows
from doeff_core_effects.http_server_effects import closing_of as closing_of
from doeff_core_effects.http_server_effects import HttpProbe as HttpProbe
from doeff_core_effects.http_server_effects import probe_for as probe_for
from doeff_core_effects.http_server_effects import probe_answer as probe_answer
from aiohttp.web_protocol import PayloadAccessError as PayloadAccessError
from doeff import run as run
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put
FILE_CHUNK_BYTES: int
BODY_CHUNK_BYTES: int
CONNECT_SECONDS: float
HTTP_READ_SECONDS: float
HOP_BY_HOP: frozenset[str]
WS_HANDSHAKE_PREFIX: str
NO_STATUS_CLOSE: int
ABNORMAL_CLOSE_CODES: frozenset[int]
NORMAL_CLOSE: int
RELAY_FAILED_CLOSE: int
EDGE_LOOPS: Incomplete
EDGE_LOOP_LOCK: Incomplete
EDGE_KEY: str
Carried = TypeVar('Carried')

def upgrade_asked(request: web.Request) -> bool:
    ...

def hop_names(connection: str) -> frozenset:
    ...

def forwarded_headers(request: web.Request, ws: bool) -> list:
    ...

def sendable_close(code: int | None) -> int:
    ...

def pump(source: web.WebSocketResponse | aiohttp.ClientWebSocketResponse, target: web.WebSocketResponse | aiohttp.ClientWebSocketResponse) -> None:
    ...

def relay_failed(line: str) -> None:
    ...

def refusal_status(request: web.Request) -> int:
    ...

class WsPeer:

    def __init__(self, ticket: str, request: web.Request, ws: web.WebSocketResponse) -> None:
        ...

class WebEdge:

    def __init__(self) -> None:
        ...

    def reset_report(self) -> None:
        ...

    def edge_loop(self) -> asyncio.AbstractEventLoop:
        ...

    def across(self, coroutine: Coroutine[object, object, Carried]) -> Carried:
        ...

    def start(self, address: HttpAddress, ws_max_bytes: int, ws_send_max_bytes: int, probes: tuple) -> HttpAddress:
        ...

    def next_arrival(self) -> HttpEvent:
        ...

    def dispatch(self, request: web.Request) -> web.StreamResponse:
        ...

    def answer_probe(self, probe: HttpProbe) -> web.Response:
        ...

    def settle(self, ticket: str, command: HttpCommand) -> None:
        ...

    def receive(self, request: web.Request) -> web.StreamResponse:
        ...

    def terminate_ws(self, request: web.Request, ticket: str) -> web.StreamResponse:
        ...

    def finish_peer(self, peer: WsPeer) -> None:
        ...

    def write_loop(self, peer: WsPeer) -> None:
        ...

    def seal(self, peer: WsPeer, unsent: int) -> None:
        ...

    def send_text(self, ticket: str, text: str) -> None:
        ...

    def close_ws(self, ticket: str, code: int, reason: str) -> None:
        ...

    def shutdown(self, reason: str, drain_seconds: float) -> None:
        ...

    def take_report(self) -> WsSendReport:
        ...

    def read_body(self, ticket: str, max_bytes: int) -> HttpBodyOutcome:
        ...

    def respond(self, request: web.Request, status: int, headers: tuple, body: HttpBodyBytes | HttpBodyFileRange | HttpNoBody, cut_off: bool) -> web.StreamResponse:
        ...

    def relay_http(self, request: web.Request, url: str) -> web.StreamResponse:
        ...

    def relay_ws(self, request: web.Request, url: str) -> web.StreamResponse:
        ...
aiohttp_http_server: _Handler
