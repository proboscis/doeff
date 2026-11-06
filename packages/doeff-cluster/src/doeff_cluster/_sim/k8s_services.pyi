# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = k8s_services.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from doeff_cluster._shared.intent.kube_service_model import EnsureKubeIngress as EnsureKubeIngress
from doeff_cluster._shared.intent.kube_service_model import EnsureKubeService as EnsureKubeService
from doeff_cluster._shared.intent.kube_service_model import KubeIngress as KubeIngress
from doeff_cluster._shared.intent.kube_service_model import KubeService as KubeService
from doeff_cluster._shared.intent.kube_service_model import KubeServiceOutcome as KubeServiceOutcome
from doeff_cluster._shared.intent.kube_service_model import KubeServiceRefused as KubeServiceRefused

class KubeServiceLog:
    services: tuple[KubeService, ...]
    ingresses: tuple[KubeIngress, ...]
    down: bool

    def __init__(self, down: bool=False) -> None:
        ...

def _held_after(held: tuple, declaration: KubeService | KubeIngress) -> _Program[tuple, object]:
    ...

def held_outcome(log: KubeServiceLog, service: KubeService) -> _Program[KubeServiceOutcome, object]:
    ...

def _held_ingress_outcome(log: KubeServiceLog, ingress: KubeIngress) -> _Program[KubeServiceOutcome, object]:
    ...

def held_kube_services(log: KubeServiceLog) -> _Handler:
    ...
