# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = scripted_ws_client.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from doeff import EffectBase as EffectBase
from doeff import Program as Program
from doeff_core_effects.ws_client_effects import WsConnect as WsConnect
from doeff_core_effects.ws_client_effects import WsReceive as WsReceive
from doeff_core_effects.ws_client_effects import WsSend as WsSend
from doeff_core_effects.ws_client_effects import WsDisconnect as WsDisconnect
from doeff_core_effects.ws_client_effects import WsDisconnectAll as WsDisconnectAll
from doeff_core_effects.ws_client_effects import ReadWsSent as ReadWsSent
from doeff_core_effects.ws_client_effects import WsLink as WsLink
from doeff_core_effects.ws_client_effects import WsConnectFailed as WsConnectFailed
from doeff_core_effects.ws_client_effects import WsText as WsText
from doeff_core_effects.ws_client_effects import WsBinary as WsBinary
from doeff_core_effects.ws_client_effects import WsLinkClosed as WsLinkClosed
from doeff_core_effects.ws_client_effects import WsFrame as WsFrame
from doeff_core_effects.ws_client_effects import WsSent as WsSent
from doeff_core_effects.ws_client_effects import WsScript as WsScript
from doeff_core_effects.ws_client_effects import ScriptedWsEndpoint as ScriptedWsEndpoint
from doeff_core_effects.ws_client_effects import ScriptedWsText as ScriptedWsText
from doeff_core_effects.ws_client_effects import ScriptedWsBinary as ScriptedWsBinary
from doeff_core_effects.ws_client_effects import ScriptedWsClosed as ScriptedWsClosed
from doeff_core_effects.ws_client_effects import ScriptedWsFrame as ScriptedWsFrame
from doeff_core_effects.ws_client_effects import WsSentText as WsSentText
from doeff_core_effects.ws_client_effects import WsSentClose as WsSentClose
from doeff_core_effects.ws_client_effects import NOT_IN_SCRIPT_REASON as NOT_IN_SCRIPT_REASON
from doeff_core_effects.ws_client_effects import SCRIPT_EXHAUSTED_REASON as SCRIPT_EXHAUSTED_REASON
from doeff_core_effects.ws_client_effects import link_name as link_name
from doeff_core_effects.ws_client_effects import closed_answer as closed_answer
from doeff_core_effects.ws_client_effects import closing_links as closing_links
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put
SCRIPTED_WS_HANDLES: tuple[type, ...]
SCRIPTED_WS_EFFECTS: tuple[()]

@dataclass(frozen=True, kw_only=True)
class ScriptedLink:
    link: str
    url: str
    endpoint: ScriptedWsEndpoint
    pending: tuple[ScriptedWsFrame, ...]

def endpoint_for(script: WsScript, url: str) -> _Program[ScriptedWsEndpoint | None, object]:
    ...

def link_of(links: tuple[ScriptedLink, ...], link: str) -> _Program[ScriptedLink | None, object]:
    ...

def without_link(links: tuple[ScriptedLink, ...], link: str) -> _Program[tuple[ScriptedLink, ...], object]:
    ...

def frame_of(link: str, scripted: ScriptedWsText | ScriptedWsBinary | ScriptedWsClosed) -> _Program[WsFrame, object]:
    ...

def next_frame(open: ScriptedLink) -> _Program[WsFrame, object]:
    ...

def replies_for(endpoint: ScriptedWsEndpoint, text: str) -> _Program[tuple[ScriptedWsFrame, ...], object]:
    ...

def scripted_ws_link_handler(script: WsScript) -> _Handler:
    ...

def scripted_ws_client(script: WsScript) -> Callable[[Program | EffectBase], Program]:
    ...
