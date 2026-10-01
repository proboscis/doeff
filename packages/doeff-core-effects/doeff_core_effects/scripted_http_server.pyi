"""scripted_http_server.hy の公開面の型(HTTP の待ち受けの effect の I/O なしの答え手 — 型検査のための宣言・実行時は
scripted_http_server.hy を読む・agora-redesign #2233)。

- scripted-http-server(Python の名 scripted_http_server)は台本 HttpScript を受け、本文の Program に被せる関数を返す
  (defhandler の展開と同じ形: 本文の答えの型をそのまま運ぶ WithHandler)。台本の出来事 HttpRequestArrived は送り元の欄 remote を
  運ぶ(型は http_server_effects.pyi の宣言)。
- defk は呼ぶと Program を返す(答えの型は Program の 1 つ目の引数)。
- 実装との食い違いは packages/doeff-core-effects/tests/test_hy_module_stubs.py が検める。
"""

from typing import Any, Protocol, TypeVar

from doeff_vm import WithHandler

from doeff import Program
from doeff_core_effects.http_server_effects import (
    HttpBody,
    HttpBodyOutcome,
    HttpCommand,
    HttpHeader,
    HttpRequestArrived,
    HttpScript,
    HttpServed,
    ScriptedBody,
    ScriptedUpstream,
    WsSendReport,
)

_A = TypeVar("_A")
_V = TypeVar("_V")

CLOSED_REASON: str
EMPTY_REPORT: WsSendReport

def upstream_for(script: HttpScript, url: str) -> Program[ScriptedUpstream | None, Any]: ...
def body_text(body: HttpBody) -> Program[str, Any]: ...
def served_of(
    script: HttpScript, command: HttpCommand, arrival: HttpRequestArrived
) -> Program[HttpServed, Any]: ...
def tally_queued(tally: WsSendReport, size: int) -> Program[WsSendReport, Any]: ...
def tally_flushed(tally: WsSendReport, size: int) -> Program[WsSendReport, Any]: ...
def tally_dropped(tally: WsSendReport, size: int, cut: bool) -> Program[WsSendReport, Any]: ...
def without_ticket(backlog: dict[str, _V], ticket: str) -> Program[dict[str, _V], Any]: ...
def declared_length(headers: tuple[HttpHeader, ...]) -> Program[int | None, Any]: ...
def scripted_body_outcome(
    declared: int | None, body: ScriptedBody | None, max_bytes: int
) -> Program[HttpBodyOutcome, Any]: ...
def bodies_by_ticket(bodies: tuple[ScriptedBody, ...]) -> Program[dict[str, ScriptedBody], Any]: ...

class _ScriptedHttpServer(Protocol):
    """本文の Program に handler を被せる関数(答えの型は本文のまま)。"""

    def __call__(self, body: Program[_A, object], /) -> WithHandler[_A]: ...

def scripted_http_server(script: HttpScript) -> _ScriptedHttpServer: ...
