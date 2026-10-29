# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = kube_services.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from urllib.parse import quote as url_quote
from doeff_core_effects import FileFailed as FileFailed
from doeff_core_effects.http_effects import HttpFailed as HttpFailed
from doeff_core_effects.http_effects import HttpRequest as HttpRequest
from doeff_core_effects.http_effects import HttpResponse as HttpResponse
from doeff_cluster.shared.intent.kube_service_model import EnsureKubeIngress as EnsureKubeIngress
from doeff_cluster.shared.intent.kube_service_model import EnsureKubeService as EnsureKubeService
from doeff_cluster.shared.intent.kube_service_model import KubeIngress as KubeIngress
from doeff_cluster.shared.intent.kube_service_model import KubeService as KubeService
from doeff_cluster.shared.intent.kube_service_model import KubeServiceOutcome as KubeServiceOutcome
from doeff_cluster.shared.intent.kube_service_model import KubeServiceRefused as KubeServiceRefused
from doeff_cluster.shared.intent.kube_service_model import KubeServiceType as KubeServiceType
from doeff_cluster.shared.protocol.supplied_values import KUBE_API as KUBE_API
from doeff_cluster.shared.protocol.supplied_values import REFUSAL_BODY_LIMIT as REFUSAL_BODY_LIMIT
from doeff_cluster.shared.protocol.supplied_values import REQUEST_TIMEOUT_SECONDS as REQUEST_TIMEOUT_SECONDS
from doeff_cluster.shared.protocol.supplied_values import SERVICE_ACCOUNT_TOKEN_FILE as SERVICE_ACCOUNT_TOKEN_FILE
from doeff_cluster.shared.protocol.supplied_values import kube_headers as kube_headers
from doeff_cluster.shared.protocol.supplied_values import service_account_text as service_account_text
from doeff_cluster.shared.protocol.supplied_values import status_field as status_field
from doeff_cluster.shared.protocol.supplied_values import text_field as text_field
FIELD_MANAGER: str
APPLY_CONTENT_TYPE: str

def service_path(service: KubeService) -> _Program[str, object]:
    ...

def _metadata_of(declaration: KubeService | KubeIngress) -> _Program[dict, object]:
    ...

def service_apply_text(service: KubeService) -> _Program[str, object]:
    ...

def _ingress_path(ingress: KubeIngress) -> _Program[str, object]:
    ...

def _ingress_apply_text(ingress: KubeIngress) -> _Program[str, object]:
    ...

def refused(place: str, reason: str) -> _Program[KubeServiceRefused, object]:
    ...

def outcome_of_answer(place: str, answer: HttpResponse | HttpFailed) -> _Program[KubeServiceOutcome, object]:
    ...

def _applied_object(place: str, path: str, body: str) -> _Program[KubeServiceOutcome, object]:
    ...
kube_services_api: _Handler
