# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = readiness_handlers.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff_hy.static_types import Handler as _Handler
from doeff_cluster.shared.intent.readiness_model import ReportReady as ReportReady
from doeff_cluster.shared.core.readiness_report import reported_readiness as reported_readiness
from doeff_cluster.shared.protocol.coordinator_route import RouteCell as RouteCell
from doeff_cluster.shared.protocol.coordinator_route import RouteOptions as RouteOptions
from doeff_cluster.shared.protocol.service_report import ServiceReport as ServiceReport
from doeff_cluster.shared.protocol.service_report import sent_report as sent_report
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler

def readiness_memory(reports: list[dict[str, object]]) -> _Handler:
    ...

def readiness_http(cell: RouteCell, options: RouteOptions, report: ServiceReport) -> _Handler:
    ...
