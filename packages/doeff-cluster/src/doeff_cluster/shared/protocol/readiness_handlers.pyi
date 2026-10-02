"""readiness_handlers.hy の公開面の型(準備できたの報告 ReportReady の答え手 — 型検査のための宣言・実行時は readiness_handlers.hy を読む・
#2777)。

readiness_handlers.hy は Hy の module なので、答え手を土台の組に並べる使い手(模擬の土台が readiness-memory で報告を積む)の strict に、
書き手に直せない Unknown の赤(Type of "readiness_memory" is unknown・Argument type is partially unknown)が出た。
host_contract.pyi と同じ形で宣言する。

- readiness-memory(Python の名 readiness_memory)は報告を積む列を受け、本文の Program に被せる関数を返す(fake — 本物と同じ形で積む)。
- readiness-http(Python の名 readiness_http)は coordinator への道と送り手の名乗りを受け、本文の Program に被せる関数を返す。
- 実装との食い違いは packages/doeff-cluster/tests/test_readiness_static_types.py が検める。
"""

from typing import Protocol, TypeVar

from doeff_vm import WithHandler

from doeff import Program
from doeff_cluster.shared.protocol.coordinator_route import RouteCell, RouteOptions
from doeff_cluster.shared.protocol.service_report import ServiceReport

_A = TypeVar("_A")

class _ReadinessHandler(Protocol):
    """本文の Program に ReportReady の答え手を被せる関数(答えの型は本文のまま)。"""

    def __call__(self, body: Program[_A, object], /) -> WithHandler[_A]: ...

def readiness_memory(reports: list[dict[str, object]]) -> _ReadinessHandler: ...
def readiness_http(cell: RouteCell, options: RouteOptions, report: ServiceReport) -> _ReadinessHandler: ...
