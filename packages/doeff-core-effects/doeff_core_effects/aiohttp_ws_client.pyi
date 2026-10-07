# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = aiohttp_ws_client.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff_hy.static_types import Handler as _Handler
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
import aiohttp as aiohttp
from aiohttp import WSMsgType as WSMsgType
from aiohttp import ClientWSTimeout as ClientWSTimeout
from doeff import EffectBase as EffectBase
from doeff import Program as Program
from doeff_core_effects.effects import Await as Await
from doeff_core_effects.http_server_effects import HttpHeader as HttpHeader
from doeff_core_effects.ws_client_effects import WsConnect as WsConnect
from doeff_core_effects.ws_client_effects import WsReceive as WsReceive
from doeff_core_effects.ws_client_effects import WsSend as WsSend
from doeff_core_effects.ws_client_effects import WsDisconnect as WsDisconnect
from doeff_core_effects.ws_client_effects import WsDisconnectAll as WsDisconnectAll
from doeff_core_effects.ws_client_effects import WsLink as WsLink
from doeff_core_effects.ws_client_effects import WsConnectFailed as WsConnectFailed
from doeff_core_effects.ws_client_effects import WsText as WsText
from doeff_core_effects.ws_client_effects import WsBinary as WsBinary
from doeff_core_effects.ws_client_effects import WsLinkClosed as WsLinkClosed
from doeff_core_effects.ws_client_effects import WsFrame as WsFrame
from doeff_core_effects.ws_client_effects import WsSent as WsSent
from doeff_core_effects.ws_client_effects import link_name as link_name
from doeff_core_effects.ws_client_effects import closed_answer as closed_answer
from doeff_core_effects.ws_client_effects import closing_links as closing_links
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put
CONNECT_SECONDS: float
HANDSHAKE_SECONDS: float
CLOSE_SECONDS: float
MAX_MESSAGE_BYTES: int
AIOHTTP_WS_HANDLES: tuple[type, ...]
AIOHTTP_WS_EFFECTS: tuple[type, ...]

@dataclass(frozen=True, kw_only=True)
class OpenLink:
    link: str
    url: str
    session: aiohttp.ClientSession
    ws: aiohttp.ClientWebSocketResponse

def new_client_session() -> aiohttp.ClientSession:
    ...

def failure_detail(error: BaseException) -> str:
    ...

def open_link(client_factory: Callable, link: str, url: str, headers: tuple[HttpHeader, ...]) -> OpenLink | WsConnectFailed:
    ...

def receive_frame(open: OpenLink) -> WsFrame:
    ...

def lost_code(ws: aiohttp.ClientWebSocketResponse) -> int | None:
    ...

def send_text(open: OpenLink, text: str) -> WsSent | WsLinkClosed:
    ...

def close_link(open: OpenLink, code: int, reason: str) -> WsLinkClosed:
    ...

def drop_link(open: OpenLink) -> None:
    ...

def aiohttp_ws_link_handler(client_factory: Callable) -> _Handler:
    ...

def aiohttp_ws_client(*, client_factory: Callable=...) -> Callable[[Program | EffectBase], Program]:
    ...
