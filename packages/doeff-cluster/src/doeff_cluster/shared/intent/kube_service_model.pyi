# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = kube_service_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from typing import ClassVar as ClassVar
from doeff import EffectBase as EffectBase
from doeff_hy._frozen import FrozenMap as FrozenMap
from doeff_cluster.shared.intent.record_spec import RecordSpec as RecordSpec
from doeff_cluster.shared.intent.record_spec import RecordMode as RecordMode

class KubeServiceType(StrEnum):
    CLUSTER_IP = 'ClusterIP'
    LOAD_BALANCER = 'LoadBalancer'

class KubeServiceOutcome(StrEnum):
    CREATED = 'created'
    APPLIED = 'applied'

@dataclass(frozen=True, kw_only=True)
class KubeServicePort:
    name: str
    port: int
    target_port: int

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class KubeService:
    namespace: str
    name: str
    type: KubeServiceType
    selector: FrozenMap[str]
    ports: tuple[KubeServicePort, ...]
    labels: FrozenMap[str] = ...
    annotations: FrozenMap[str] = ...
    load_balancer_class: str | None = None

    def __post_init__(self) -> None:
        ...

class KubeServiceRefused(RuntimeError):
    ...

@dataclass(frozen=True)
class EnsureKubeService(EffectBase[KubeServiceOutcome]):
    __record_spec__: ClassVar[RecordSpec]
    service: KubeService
