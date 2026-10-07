# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = kube_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass
from doeff import EffectBase as EffectBase
from doeff_hy.table import TableWrite as TableWrite
from doeff_cluster.coordinator.intent.cluster_model import DeploymentSeen as DeploymentSeen
from doeff_cluster.coordinator.intent.cluster_model import DeploymentUnreadable as DeploymentUnreadable
from doeff_cluster.coordinator.intent.cluster_model import NodeLabelsSeen as NodeLabelsSeen
from doeff_cluster.coordinator.intent.cluster_model import NodeLabelsUnreadable as NodeLabelsUnreadable

class KubeUnavailable(Exception):
    ...

@dataclass(frozen=True)
class ScaleDeployment(EffectBase):
    namespace: str
    name: str
    replicas: int
    dry_run: bool = False

@dataclass(frozen=True)
class AnnotateDeployment(EffectBase):
    namespace: str
    name: str
    annotations: dict

@dataclass(frozen=True)
class FollowDeployments(EffectBase):
    keys: tuple[str, ...]
    now_ms: int

@dataclass(frozen=True)
class StartKubeReads(EffectBase):
    nodes: tuple[str, ...]
    started_ms: int

@dataclass(frozen=True)
class CollectKubeReads(EffectBase):
    now_ms: int
    name_after_ms: int

@dataclass(frozen=True, kw_only=True)
class KubeReadsIdle:
    ...

@dataclass(frozen=True, kw_only=True)
class KubeReadsRunning:
    started_ms: int
    overdue: bool

@dataclass(frozen=True, kw_only=True)
class KubeReadsDone:
    nodes: tuple[TableWrite[NodeLabelsSeen | NodeLabelsUnreadable], ...]
