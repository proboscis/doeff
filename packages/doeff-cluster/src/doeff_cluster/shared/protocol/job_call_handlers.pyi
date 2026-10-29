# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = job_call_handlers.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from collections.abc import Callable as Callable
from urllib.parse import quote as url_quote
from urllib.parse import urlencode as urlencode
from doeff_core_effects import Ask as Ask
from doeff_core_effects.http_effects import HttpFailed as HttpFailed
from doeff_core_effects.http_effects import HttpRequest as HttpRequest
from doeff_core_effects.http_effects import HttpResponse as HttpResponse
from doeff_cluster.shared.intent.job_call_model import CallJob as CallJob
from doeff_cluster.shared.intent.job_call_model import JobAnswer as JobAnswer
from doeff_cluster.shared.intent.job_call_model import JobField as JobField
from doeff_cluster.shared.intent.job_call_model import JobRequest as JobRequest
from doeff_cluster.shared.intent.job_call_model import JobUnreachable as JobUnreachable
PATH_SAFE: str
CONTENT_TYPE: str
NO_CONTENT_TYPE: str

@dataclass(frozen=True, kw_only=True)
class JobDestination:
    api: str
    root: str
    environ: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class Counterpart:
    api: str
    answer: Callable[..., object]

    def __post_init__(self) -> None:
        ...

def job_target(root: str, request: JobRequest) -> _Program[str, object]:
    ...

def job_answer_of(response: HttpResponse | HttpFailed) -> _Program[JobAnswer | JobUnreachable, object]:
    ...

def sent_call(destination: JobDestination, request: JobRequest) -> _Program[JobAnswer | JobUnreachable, object]:
    ...

def job_call_sender(destinations: tuple[JobDestination, ...]) -> _Handler:
    ...

def job_call_handover(counterparts: tuple[Counterpart, ...]) -> _Handler:
    ...
