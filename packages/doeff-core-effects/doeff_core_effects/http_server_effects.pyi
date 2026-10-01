"""http_server_effects.hy の公開面の型(型検査のための宣言 — 実行時は http_server_effects.hy を読む・agora-redesign #2323)。

http_server_effects.hy は Hy の module なので、型の宣言が無いと pyright は中を読めず、`from doeff_core_effects.http_server_effects
import HttpListen` の名が全部 Unknown になる(消費者の strict の型検査で、書き手に直せない赤が連なる)。ここで型を宣言する。

- defrecord は frozen で keyword だけの dataclass。
- effect(`(defclass [(dataclass :frozen True)] … [EffectBase])`)は位置でも渡せる frozen の dataclass で、`EffectBase[答えの型]` の
  下位の型。答えの型は .hy の頭の註のとおり(命令は撃つだけで答えは None)。
- `(val 名 (| …))` の union は型の別名。
- defk は呼ぶと Program を返す(答えの型は Program の 1 つ目の引数)。
- 実装との食い違いは packages/doeff-core-effects/tests/test_hy_module_stubs.py が名・欄の名と順・既定値の有無・引数の名で検める。
"""

from dataclasses import dataclass
from typing import Any, TypeAlias

from doeff_vm import EffectBase

from doeff import Program

DEFAULT_WS_MAX_BYTES: int
DEFAULT_WS_SEND_MAX_BYTES: int
DEFAULT_DRAIN_SECONDS: float
WS_CLOSE_NORMAL: int
WS_CLOSE_ABNORMAL: int
WS_REFUSAL_TEXT: str
WS_CUT_REASON: str

# --- 値 ---

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

#: HttpNextRequest の答えの union。
WsEvent: TypeAlias = WsOpened | WsTextArrived | WsBinaryArrived | WsClosed
HttpEvent: TypeAlias = (
    HttpRequestArrived | WsOpened | WsTextArrived | WsBinaryArrived | WsClosed | HttpServerClosed
)

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
class HttpNoBody: ...

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

#: HttpReadBody の答えの union。
HttpBodyOutcome: TypeAlias = HttpBodyRead | HttpBodyTooLarge | HttpBodyFailed

# --- effect ---

@dataclass(frozen=True)
class HttpListen(EffectBase[HttpAddress]):
    address: HttpAddress
    ws_max_bytes: int = ...
    ws_send_max_bytes: int = ...

@dataclass(frozen=True)
class HttpNextRequest(EffectBase[HttpEvent]): ...

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
class HttpShutdown(EffectBase[None]):
    reason: str
    drain_seconds: float = ...

@dataclass(frozen=True)
class TakeWsSendReport(EffectBase[WsSendReport]): ...

#: 札の要求への命令の union。
HttpCommand: TypeAlias = HttpRespond | HttpForward | WsForward | WsAccept

# --- 答え手が共に呼ぶ判断 ---

def carries_content(method: str, status: int) -> Program[bool, Any]: ...
def ws_refusal_status(method: str, upgrade: bool) -> Program[int | None, Any]: ...
def send_overflows(held: int, size: int, limit: int) -> Program[bool, Any]: ...

@dataclass(frozen=True, kw_only=True)
class WsCloseFrame:
    code: int
    reason: str

def closing_of(
    cut: str | None, sent: WsCloseFrame | None, received: WsCloseFrame | None, lost: int | None
) -> Program[WsCloseFrame, Any]: ...

# --- 台本の語彙(scripted-http-server) ---

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
    upstreams: tuple[ScriptedUpstream, ...] = ()
    stalled: frozenset[str] = ...
    bodies: tuple[ScriptedBody, ...] = ()

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
class ReadHttpServed(EffectBase[tuple[HttpServed | WsTextSent | WsCloseSent, ...]]): ...

@dataclass(frozen=True)
class AppendHttpScript(EffectBase[None]):
    arrivals: tuple[HttpRequestArrived | WsTextArrived | WsBinaryArrived | WsClosed, ...]
    bodies: tuple[ScriptedBody, ...] = ()
