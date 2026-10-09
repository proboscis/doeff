# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = http_server_effects.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from doeff import EffectBase as EffectBase
from doeff import Program as Program
from doeff import run as run
DEFAULT_WS_MAX_BYTES: int
DEFAULT_WS_SEND_MAX_BYTES: int
DEFAULT_DRAIN_SECONDS: float
WS_CLOSE_NORMAL: int
WS_CLOSE_ABNORMAL: int
WS_REFUSAL_TEXT: str
WS_CUT_REASON: str

@dataclass(frozen=True, kw_only=True)
class HttpAddress:
    host: str
    port: int

@dataclass(frozen=True, kw_only=True)
class HttpHeader:
    name: str
    value: str

@dataclass(frozen=True, kw_only=True)
class HttpRequestArrived:
    ticket: str
    method: str
    path: str
    target: str
    headers: tuple[HttpHeader, ...]
    upgrade: bool
    received_at: float | None = None
    remote: str | None = None

@dataclass(frozen=True, kw_only=True)
class WsOpened:
    ticket: str
    received_at: float | None = None

@dataclass(frozen=True, kw_only=True)
class WsTextArrived:
    ticket: str
    text: str
    received_at: float | None = None

@dataclass(frozen=True, kw_only=True)
class WsBinaryArrived:
    ticket: str
    data: bytes
    received_at: float | None = None

@dataclass(frozen=True, kw_only=True)
class WsClosed:
    ticket: str
    code: int
    reason: str
    received_at: float | None = None

@dataclass(frozen=True, kw_only=True)
class HttpServerClosed:
    reason: str
WsEvent: TypeAlias = WsOpened | WsTextArrived | WsBinaryArrived | WsClosed
HttpEvent: TypeAlias = HttpRequestArrived | WsOpened | WsTextArrived | WsBinaryArrived | WsClosed | HttpServerClosed

@dataclass(frozen=True, kw_only=True)
class WsSendReport:
    queued_frames: int
    queued_bytes: int
    flushed_bytes: int
    flush_seconds: tuple[float, ...]
    dropped_bytes: int
    cuts: int
FLUSH_SAMPLES_LIMIT: int

@dataclass(frozen=True, kw_only=True)
class HttpBodyBytes:
    data: bytes

@dataclass(frozen=True, kw_only=True)
class HttpBodyFileRange:
    path: str
    start: int
    length: int

@dataclass(frozen=True, kw_only=True)
class HttpNoBody:
    ...
HttpBody: TypeAlias = HttpBodyBytes | HttpBodyFileRange | HttpNoBody

@dataclass(frozen=True, kw_only=True)
class HttpBodyRead:
    data: bytes

@dataclass(frozen=True, kw_only=True)
class HttpBodyTooLarge:
    declared: int | None

@dataclass(frozen=True, kw_only=True)
class HttpBodyFailed:
    reason: str
HttpBodyOutcome: TypeAlias = HttpBodyRead | HttpBodyTooLarge | HttpBodyFailed

@dataclass(frozen=True, kw_only=True)
class HttpProbeAnswer:
    status: int
    headers: tuple[HttpHeader, ...]
    body: bytes

@dataclass(frozen=True, kw_only=True)
class HttpProbe:
    path: str
    answer: Program[HttpProbeAnswer, object]

@dataclass(frozen=True)
class HttpListen(EffectBase[HttpAddress]):
    address: HttpAddress
    ws_max_bytes: int = ...
    ws_send_max_bytes: int = ...
    probes: tuple[HttpProbe, ...] = ...
    share_port: bool = False

@dataclass(frozen=True)
class HttpNextRequest(EffectBase[HttpEvent]):
    ...

@dataclass(frozen=True)
class HttpRespond(EffectBase[None]):
    ticket: str
    status: int
    headers: tuple[HttpHeader, ...]
    body: HttpBody

@dataclass(frozen=True)
class HttpReadBody(EffectBase[HttpBodyOutcome]):
    ticket: str
    max_bytes: int

@dataclass(frozen=True)
class HttpForward(EffectBase[None]):
    ticket: str
    url: str

@dataclass(frozen=True)
class WsForward(EffectBase[None]):
    ticket: str
    url: str

@dataclass(frozen=True)
class WsAccept(EffectBase[None]):
    ticket: str

@dataclass(frozen=True)
class WsSendText(EffectBase[None]):
    ticket: str
    text: str

@dataclass(frozen=True)
class WsClose(EffectBase[None]):
    ticket: str
    code: int
    reason: str

@dataclass(frozen=True)
class HttpStopListening(EffectBase[None]):
    ...

@dataclass(frozen=True)
class HttpShutdown(EffectBase[None]):
    reason: str
    drain_seconds: float = ...
    close_code: int = ...

@dataclass(frozen=True)
class TakeWsSendReport(EffectBase[WsSendReport]):
    ...
HttpCommand: TypeAlias = HttpRespond | HttpForward | WsForward | WsAccept

def carries_content(method: str, status: int) -> _Program[bool, object]:
    ...

def ws_refusal_status(method: str, upgrade: bool) -> _Program[int | None, object]:
    ...

def send_overflows(held: int, size: int, limit: int) -> _Program[bool, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class WsCloseFrame:
    code: int
    reason: str

def closing_of(cut: str | None, sent: WsCloseFrame | None, received: WsCloseFrame | None, lost: int | None) -> _Program[WsCloseFrame, object]:
    ...

def probe_for(probes: tuple[HttpProbe, ...], method: str, path: str) -> _Program[HttpProbe | None, object]:
    ...

def probe_failure(path: str, reason: str) -> _Program[HttpProbeAnswer, object]:
    ...

def probe_answer(probe: HttpProbe) -> HttpProbeAnswer:
    ...

@dataclass(frozen=True, kw_only=True)
class ScriptedUpstream:
    base: str
    http_status: int
    accepts_ws: bool

@dataclass(frozen=True, kw_only=True)
class ScriptedBody:
    ticket: str
    data: bytes
    failed: str | None = None

@dataclass(frozen=True, kw_only=True)
class HttpScript:
    arrivals: tuple[HttpRequestArrived | WsTextArrived | WsBinaryArrived | WsClosed, ...]
    upstreams: tuple[ScriptedUpstream, ...] = ...
    stalled: frozenset[str] = ...
    bodies: tuple[ScriptedBody, ...] = ...

@dataclass(frozen=True, kw_only=True)
class HttpServed:
    ticket: str
    command: HttpCommand
    status: int
    body: str

@dataclass(frozen=True, kw_only=True)
class WsTextSent:
    ticket: str
    text: str

@dataclass(frozen=True, kw_only=True)
class WsCloseSent:
    ticket: str
    code: int
    reason: str

@dataclass(frozen=True)
class ReadHttpServed(EffectBase[tuple[HttpServed | WsTextSent | WsCloseSent, ...]]):
    ...

@dataclass(frozen=True)
class AppendHttpScript(EffectBase[None]):
    arrivals: tuple[HttpRequestArrived | WsTextArrived | WsBinaryArrived | WsClosed, ...]
    bodies: tuple[ScriptedBody, ...] = ...
