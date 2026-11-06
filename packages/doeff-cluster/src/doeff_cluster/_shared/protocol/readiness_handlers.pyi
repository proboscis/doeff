# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = readiness_handlers.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff_hy._static_types import Handler as _Handler
from doeff_cluster._shared.intent.readiness_model import ReportReady as ReportReady
from doeff_cluster._shared.intent.readiness_model import ReadinessClaim as ReadinessClaim
from doeff_cluster._shared.core.readiness_report import reported_readiness as reported_readiness
from doeff_cluster._shared.protocol.coordinator_route import RouteCell as RouteCell
from doeff_cluster._shared.protocol.coordinator_route import RouteOptions as RouteOptions
from doeff_cluster._shared.protocol.service_report import ServiceReport as ServiceReport
from doeff_cluster._shared.protocol.service_report import sent_report as sent_report

class ReadinessLog:
    claims: tuple[ReadinessClaim, ...]

    def __init__(self) -> None:
        ...

def readiness_claims(log: ReadinessLog) -> _Handler:
    ...

def readiness_http(cell: RouteCell, options: RouteOptions, report: ServiceReport) -> _Handler:
    ...
