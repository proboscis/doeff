# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = scripted_http_server.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from doeff_core_effects.http_server_effects import HttpListen as HttpListen
from doeff_core_effects.http_server_effects import HttpNextRequest as HttpNextRequest
from doeff_core_effects.http_server_effects import HttpRespond as HttpRespond
from doeff_core_effects.http_server_effects import HttpForward as HttpForward
from doeff_core_effects.http_server_effects import WsForward as WsForward
from doeff_core_effects.http_server_effects import WsAccept as WsAccept
from doeff_core_effects.http_server_effects import WsSendText as WsSendText
from doeff_core_effects.http_server_effects import WsClose as WsClose
from doeff_core_effects.http_server_effects import HttpShutdown as HttpShutdown
from doeff_core_effects.http_server_effects import HttpStopListening as HttpStopListening
from doeff_core_effects.http_server_effects import TakeWsSendReport as TakeWsSendReport
from doeff_core_effects.http_server_effects import WsSendReport as WsSendReport
from doeff_core_effects.http_server_effects import ReadHttpServed as ReadHttpServed
from doeff_core_effects.http_server_effects import AppendHttpScript as AppendHttpScript
from doeff_core_effects.http_server_effects import HttpServed as HttpServed
from doeff_core_effects.http_server_effects import WsTextSent as WsTextSent
from doeff_core_effects.http_server_effects import WsCloseSent as WsCloseSent
from doeff_core_effects.http_server_effects import WsOpened as WsOpened
from doeff_core_effects.http_server_effects import WsClosed as WsClosed
from doeff_core_effects.http_server_effects import HttpServerClosed as HttpServerClosed
from doeff_core_effects.http_server_effects import HttpScript as HttpScript
from doeff_core_effects.http_server_effects import ScriptedUpstream as ScriptedUpstream
from doeff_core_effects.http_server_effects import HttpBodyBytes as HttpBodyBytes
from doeff_core_effects.http_server_effects import HttpBodyFileRange as HttpBodyFileRange
from doeff_core_effects.http_server_effects import HttpNoBody as HttpNoBody
from doeff_core_effects.http_server_effects import DEFAULT_WS_SEND_MAX_BYTES as DEFAULT_WS_SEND_MAX_BYTES
from doeff_core_effects.http_server_effects import FLUSH_SAMPLES_LIMIT as FLUSH_SAMPLES_LIMIT
from doeff_core_effects.http_server_effects import HttpReadBody as HttpReadBody
from doeff_core_effects.http_server_effects import HttpBodyRead as HttpBodyRead
from doeff_core_effects.http_server_effects import HttpBodyTooLarge as HttpBodyTooLarge
from doeff_core_effects.http_server_effects import HttpBodyFailed as HttpBodyFailed
from doeff_core_effects.http_server_effects import HttpBodyOutcome as HttpBodyOutcome
from doeff_core_effects.http_server_effects import HttpRequestArrived as HttpRequestArrived
from doeff_core_effects.http_server_effects import ScriptedBody as ScriptedBody
from doeff_core_effects.http_server_effects import WS_CUT_REASON as WS_CUT_REASON
from doeff_core_effects.http_server_effects import WS_REFUSAL_TEXT as WS_REFUSAL_TEXT
from doeff_core_effects.http_server_effects import WsCloseFrame as WsCloseFrame
from doeff_core_effects.http_server_effects import carries_content as carries_content
from doeff_core_effects.http_server_effects import ws_refusal_status as ws_refusal_status
from doeff_core_effects.http_server_effects import send_overflows as send_overflows
from doeff_core_effects.http_server_effects import closing_of as closing_of
from doeff_core_effects.http_server_effects import HttpProbe as HttpProbe
from doeff_core_effects.http_server_effects import HttpProbeAnswer as HttpProbeAnswer
from doeff_core_effects.http_server_effects import probe_for as probe_for
from doeff_core_effects.http_server_effects import probe_answer as probe_answer
from doeff_core_effects.http_server_effects import WsTextArrived as WsTextArrived
from doeff_core_effects.http_server_effects import WsBinaryArrived as WsBinaryArrived
from doeff_core_effects.http_server_effects import HttpHeader as HttpHeader
from doeff_core_effects.file_effects import ReadBytes as ReadBytes
from doeff_core_effects.file_effects import FileFailed as FileFailed
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put
CLOSED_REASON: str
EMPTY_REPORT: WsSendReport

def upstream_for(script: HttpScript, url: str) -> _Program[ScriptedUpstream | None, object]:
    ...

def body_text(body: HttpBodyBytes | HttpBodyFileRange | HttpNoBody) -> _Program[str, object]:
    ...

def served_of(script: HttpScript, command: HttpRespond | HttpForward | WsForward | WsAccept, arrival: HttpRequestArrived) -> _Program[HttpServed, object]:
    ...

def tally_queued(tally: WsSendReport, size: int) -> _Program[WsSendReport, object]:
    ...

def tally_flushed(tally: WsSendReport, size: int) -> _Program[WsSendReport, object]:
    ...

def tally_dropped(tally: WsSendReport, size: int, cut: bool) -> _Program[WsSendReport, object]:
    ...

def without_ticket[V](backlog: dict[str, V], ticket: str) -> _Program[dict[str, V], object]:
    ...

def declared_length(headers: tuple[HttpHeader, ...]) -> _Program[int | None, object]:
    ...

def scripted_body_outcome(declared: int | None, body: ScriptedBody | None, max_bytes: int) -> _Program[HttpBodyOutcome, object]:
    ...

def bodies_by_ticket(bodies: tuple[ScriptedBody, ...]) -> _Program[dict[str, ScriptedBody], object]:
    ...

@dataclass(frozen=True, kw_only=True)
class ProbeHit:
    arrival: HttpRequestArrived
    probe: HttpProbe

def probe_hit(probes: tuple[HttpProbe, ...], event: HttpRequestArrived | WsTextArrived | WsBinaryArrived | WsClosed | WsOpened) -> _Program[ProbeHit | None, object]:
    ...

def probe_served(arrival: HttpRequestArrived, answer: HttpProbeAnswer) -> _Program[HttpServed, object]:
    ...

def scripted_http_server(script: HttpScript) -> _Handler:
    ...
