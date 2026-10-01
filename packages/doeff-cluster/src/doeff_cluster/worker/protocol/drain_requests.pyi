"""worker/protocol/drain_requests.hy の公開面の型(型検査のための宣言 — 実行時は drain_requests.hy を読む)。

drain_requests.hy は Hy の module なので、pyright は中を読めず、名が全部 Unknown になる。使い手の repo の模擬の世界が drain の頼みを
本番の答え手と同じ綴り(drain-request)で要求の形にすると、書き手に直せない赤(Type of "drain_request" is unknown)が出た(#2541)。
ここで型を宣言する(launch.pyi・request_bodies.pyi と同じ形)。

- defn 相当(drain-request — deff)は普通の関数で、答え = #(method path query 本文)。defk(call-answer)は呼ぶと Program を返す。
  coordinator-calls は handler の値。
"""

from typing import Protocol, TypeVar

from doeff_vm import WithHandler

from doeff import Program
from doeff_core_effects.http_effects import HttpFailed, HttpResponse

_A = TypeVar("_A")

MODULE_TAGS: dict[str, str]

class _CallsHandler(Protocol):
    """本文の Program に handler を被せる関数(答えの型は本文のまま — host_contract.pyi の _HostHandler と同じ形)。"""

    def __call__(self, body: Program[_A, object], /) -> WithHandler[_A]: ...

def drain_request(name: str, ttl_seconds: float, own_boot: str | None) -> tuple[str, str, dict[str, str], dict[str, object]]: ...
def call_answer(answer: HttpResponse | HttpFailed | None) -> Program[dict[str, object], object]: ...
# cell・options の型(coordinator_route の RouteCell・RouteOptions)は型の宣言の無い Hy の module に在るので object で受ける
# (宣言の無い名を import すると、この stub の側が Unknown になる)。
def coordinator_calls(cell: object, options: object) -> _CallsHandler: ...
