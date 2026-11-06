# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = job_reach_handlers.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from doeff_core_effects import Ask as Ask
from doeff_core_effects import HttpFailed as HttpFailed
from doeff_core_effects import HttpFailureKind as HttpFailureKind
from doeff_core_effects import HttpRequest as HttpRequest
from doeff_core_effects import HttpResponse as HttpResponse
from doeff_core_effects import HostNotResolved as HostNotResolved
from doeff_core_effects import HostResolved as HostResolved
from doeff_core_effects import ResolveFailureKind as ResolveFailureKind
from doeff_core_effects import ResolveHost as ResolveHost
from urllib.parse import urlsplit as urlsplit
from doeff_cluster._shared.intent.job_call_model import JobAnswer as JobAnswer
from doeff_cluster._shared.intent.job_call_model import JobRequest as JobRequest
from doeff_cluster._shared.intent.job_call_model import JobUnreachable as JobUnreachable
from doeff_cluster._shared.intent.job_reach_model import JobNoWay as JobNoWay
from doeff_cluster._shared.intent.job_reach_model import JobNotReached as JobNotReached
from doeff_cluster._shared.intent.job_reach_model import JobNotResolved as JobNotResolved
from doeff_cluster._shared.intent.job_reach_model import JobReached as JobReached
from doeff_cluster._shared.intent.job_reach_model import JobResolved as JobResolved
from doeff_cluster._shared.intent.job_reach_model import JobWay as JobWay
from doeff_cluster._shared.intent.job_reach_model import ReachFailure as ReachFailure
from doeff_cluster._shared.intent.job_reach_model import ReachJob as ReachJob
from doeff_cluster._shared.intent.job_reach_model import ResolveFailure as ResolveFailure
from doeff_cluster._shared.intent.job_reach_model import ResolveJob as ResolveJob
from doeff_cluster._shared.protocol.job_call_handlers import Counterpart as Counterpart
from doeff_cluster._shared.protocol.job_call_handlers import job_target as job_target

@dataclass(frozen=True, kw_only=True)
class ReachDestination:
    api: str
    root: str
    environ: str
    address_environ: str = ''

    def __post_init__(self) -> None:
        ...

def reach_failure_of(kind: HttpFailureKind) -> _Program[ReachFailure, object]:
    ...

def reach_answer_of(response: HttpResponse | HttpFailed) -> _Program[JobReached | JobNotReached, object]:
    ...

def way_environ(destination: ReachDestination, way: JobWay) -> _Program[str, object]:
    ...

def sent_reach(destination: ReachDestination, request: JobRequest, way: JobWay, connect_patience: float | None) -> _Program[JobReached | JobNotReached | JobNoWay, object]:
    ...

def resolve_failure_of(kind: ResolveFailureKind) -> _Program[ResolveFailure, object]:
    ...

def resolved_name(destination: ReachDestination, patience: float) -> _Program[JobResolved | JobNotResolved | JobNoWay, object]:
    ...

def handed_reach(counterpart: Counterpart, request: JobRequest) -> _Program[JobReached | JobNotReached, object]:
    ...

def job_reach_sender(destinations: tuple[ReachDestination, ...]) -> _Handler:
    ...

def job_reach_handover(counterparts: tuple[Counterpart, ...]) -> _Handler:
    ...
