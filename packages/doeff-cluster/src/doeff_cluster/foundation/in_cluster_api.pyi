# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = in_cluster_api.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from urllib.parse import SplitResult as SplitResult
from urllib.parse import urlsplit as urlsplit
from doeff import Program as Program
from doeff import EffectBase as EffectBase
from doeff import with_handlers as with_handlers
from doeff_core_effects.http_effects import HttpFailed as HttpFailed
from doeff_core_effects.http_effects import HttpFailureKind as HttpFailureKind
from doeff_core_effects.http_effects import HttpRequest as HttpRequest
from doeff_core_effects.http_handlers import http_production_handler as http_production_handler
from doeff_core_effects.http_handlers import http_client_factory as http_client_factory
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
SERVICE_ACCOUNT_CA_PATH: str
CLUSTER_API: str
CA_MISSING: str

def in_cluster_api_connected(ca_path: str, body: Program | EffectBase) -> _Program[Incomplete, object]:
    ...

class ClusterCaMissing(FileNotFoundError):
    url: str
    ca_path: str

    def __init__(self, url: str, ca_path: str) -> None:
        ...

def effective_port(parts: SplitResult) -> _Program[int | None, object]:
    ...

def hyx_readable_portXquestion_markX(parts: SplitResult) -> _Program[bool, object]:
    ...

def hyx_same_originXquestion_markX(url: str, api: str) -> _Program[bool, object]:
    ...

def cluster_ca_answer(ca_path: str, request: HttpRequest) -> _Program[Incomplete, object]:
    ...

def routed_answer(api: str, ca_path: str, request: HttpRequest) -> _Program[Incomplete, object]:
    ...

def in_cluster_api_routed(api: str, ca_path: str) -> _Handler:
    ...
