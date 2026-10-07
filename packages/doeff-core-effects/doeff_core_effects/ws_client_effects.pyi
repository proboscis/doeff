# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = ws_client_effects.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from _typeshed import Incomplete
from doeff import Program as _Program
from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass
from doeff import EffectBase as EffectBase
from doeff import Program as Program
from doeff_core_effects.http_server_effects import HttpHeader as HttpHeader
WS_LINK_CLOSE_NORMAL: int
WS_LINK_CLOSE_GOING_AWAY: int
WS_LINK_CLOSE_ABNORMAL: int
UNKNOWN_LINK_REASON: str
SCRIPT_EXHAUSTED_REASON: str
NOT_IN_SCRIPT_REASON: str
SCOPE_ENDED_REASON: str

@dataclass(frozen=True, kw_only=True)
class WsLink:
    link: str
    url: str

@dataclass(frozen=True, kw_only=True)
class WsConnectFailed:
    url: str
    reason: str
    status: int | None
WsConnectOutcome: TypeAlias = WsLink | WsConnectFailed

@dataclass(frozen=True, kw_only=True)
class WsText:
    link: str
    text: str

@dataclass(frozen=True, kw_only=True)
class WsBinary:
    link: str
    data: bytes

@dataclass(frozen=True, kw_only=True)
class WsLinkClosed:
    link: str
    code: int | None
    reason: str
WsFrame: TypeAlias = WsText | WsBinary | WsLinkClosed

@dataclass(frozen=True, kw_only=True)
class WsSent:
    link: str
WsSendOutcome: TypeAlias = WsSent | WsLinkClosed

@_doeff_dataclass(frozen=True)
class WsConnect(_doeff_effect_base[WsLink | WsConnectFailed]):
    url: str
    headers: tuple[HttpHeader, ...] = ...

@_doeff_dataclass(frozen=True)
class WsReceive(_doeff_effect_base[WsText | WsBinary | WsLinkClosed]):
    link: str

@_doeff_dataclass(frozen=True)
class WsSend(_doeff_effect_base[WsSent | WsLinkClosed]):
    link: str
    text: str

@_doeff_dataclass(frozen=True)
class WsDisconnect(_doeff_effect_base[WsLinkClosed]):
    link: str
    code: int = ...
    reason: str = ''

@_doeff_dataclass(frozen=True)
class WsDisconnectAll(_doeff_effect_base[tuple[WsLinkClosed, ...]]):
    code: int = ...
    reason: str = ...

def link_name(n: int) -> _Program[str, object]:
    ...

def closed_answer(ended: tuple[WsLinkClosed, ...], link: str) -> _Program[WsLinkClosed, object]:
    ...

def closing_links(program: Program | EffectBase) -> _Program[Incomplete, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class ScriptedWsText:
    text: str

@dataclass(frozen=True, kw_only=True)
class ScriptedWsBinary:
    data: bytes

@dataclass(frozen=True, kw_only=True)
class ScriptedWsClosed:
    code: int
    reason: str
ScriptedWsFrame: TypeAlias = ScriptedWsText | ScriptedWsBinary | ScriptedWsClosed

@dataclass(frozen=True, kw_only=True)
class ScriptedWsReply:
    contains: str
    frames: tuple[ScriptedWsFrame, ...]

@dataclass(frozen=True, kw_only=True)
class ScriptedWsEndpoint:
    url: str
    frames: tuple[ScriptedWsFrame, ...] = ...
    replies: tuple[ScriptedWsReply, ...] = ...
    then_close: ScriptedWsClosed | None = None

@dataclass(frozen=True, kw_only=True)
class WsScript:
    endpoints: tuple[ScriptedWsEndpoint, ...]

@dataclass(frozen=True, kw_only=True)
class WsSentText:
    link: str
    text: str

@dataclass(frozen=True, kw_only=True)
class WsSentClose:
    link: str
    code: int
    reason: str
WsSentRecord: TypeAlias = WsSentText | WsSentClose

@_doeff_dataclass(frozen=True)
class ReadWsSent(_doeff_effect_base[tuple[WsSentText | WsSentClose, ...]]):
    ...
