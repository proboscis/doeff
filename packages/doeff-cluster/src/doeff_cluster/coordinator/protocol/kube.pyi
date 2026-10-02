# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = kube.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from collections.abc import Callable as Callable
from collections.abc import Mapping as Mapping
from dataclasses import dataclass as dataclass
from typing import Protocol as Protocol
from doeff_hy.wire import parse as parse
from doeff_hy.wire import Malformed as Malformed
from doeff_hy.json_value import OpaqueJson as OpaqueJson
from doeff_hy.frozen import freeze_json_text as freeze_json_text
from doeff_hy.frozen import thaw_json as thaw_json
from doeff_hy.table import Table as Table
from doeff_hy.table import TableWrite as TableWrite
from doeff_hy.table import table_of as table_of
from doeff_cluster.coordinator.intent.cluster_model import DeploymentReading as DeploymentReading
from doeff_cluster.coordinator.intent.cluster_model import DeploymentSeen as DeploymentSeen
from doeff_cluster.coordinator.intent.cluster_model import DeploymentUnreadable as DeploymentUnreadable
from doeff_cluster.coordinator.intent.cluster_model import NodeLabelsSeen as NodeLabelsSeen
from doeff_cluster.coordinator.intent.cluster_model import NodeLabelsUnreadable as NodeLabelsUnreadable
from doeff_cluster.coordinator.intent.kube_model import ScaleDeployment as ScaleDeployment
from doeff_cluster.coordinator.intent.kube_model import AnnotateDeployment as AnnotateDeployment
from doeff_cluster.coordinator.intent.kube_model import KubeUnavailable as KubeUnavailable
from doeff_cluster.coordinator.intent.kube_model import StartKubeReads as StartKubeReads
from doeff_cluster.coordinator.intent.kube_model import CollectKubeReads as CollectKubeReads
from doeff_cluster.coordinator.intent.kube_model import KubeReadsIdle as KubeReadsIdle
from doeff_cluster.coordinator.intent.kube_model import KubeReadsRunning as KubeReadsRunning
from doeff_cluster.coordinator.intent.kube_model import KubeReadsDone as KubeReadsDone
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler

def deployment_view(body: Mapping[str, object]) -> _Program[dict[str, object], object]:
    ...

def deployment_reading(view: Mapping[str, object]) -> _Program[DeploymentReading, object]:
    ...

def node_labels_table(labels: Mapping[str, object]) -> _Program[Table[str], object]:
    ...

class KubeCalls(Protocol):

    def node_labels(self, node: str) -> OpaqueJson:
        ...

    def read(self, namespace: str, name: str) -> OpaqueJson:
        ...

    def scale(self, namespace: str, name: str, replicas: int, dry_run: bool) -> int:
        ...

    def annotate(self, namespace: str, name: str, annotations: dict) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class KubeBodyRead:
    name: str
    body: OpaqueJson

@dataclass(frozen=True, kw_only=True)
class KubeReadFailed:
    name: str
    error: str

class KubeReadBatch:
    deployments: tuple[str, ...]
    nodes: tuple[str, ...]
    started_ms: int
    named: Incomplete
    done: Incomplete
    deployment_results: tuple[KubeBodyRead | KubeReadFailed, ...]
    node_results: tuple[KubeBodyRead | KubeReadFailed, ...]

    def __init__(self, deployments: tuple[str, ...], nodes: tuple[str, ...], started_ms: int) -> None:
        ...

    def read_one(self, read: Callable[[], OpaqueJson], name: str) -> KubeBodyRead | KubeReadFailed:
        ...

    def read_with(self, read_deployment: Callable[[str], OpaqueJson], read_node: Callable[[str], OpaqueJson]) -> None:
        ...

class KubeReadBatches:
    current: KubeReadBatch | None

    def __init__(self) -> None:
        ...

    def begin(self, deployments: tuple[str, ...], nodes: tuple[str, ...], started_ms: int) -> KubeReadBatch | None:
        ...

def deployment_observation_of(result: KubeBodyRead | KubeReadFailed, at: int) -> _Program[DeploymentSeen | DeploymentUnreadable, object]:
    ...

def node_labels_observation_of(result: KubeBodyRead | KubeReadFailed, at: int) -> _Program[NodeLabelsSeen | NodeLabelsUnreadable, object]:
    ...

def kube_reads_done(batch: KubeReadBatch) -> _Program[KubeReadsDone, object]:
    ...

def collected_reads(batches: KubeReadBatches, now_ms: int, name_after_ms: int, held: bool) -> _Program[KubeReadsIdle | KubeReadsRunning | KubeReadsDone, object]:
    ...

def kube_api(client: KubeCalls, batches: KubeReadBatches) -> _Handler:
    ...

def kube_unavailable(reason: str, batches: KubeReadBatches) -> _Handler:
    ...

class KubeMemory:
    deployments: dict
    calls: Incomplete
    down: Incomplete
    nodes: Incomplete
    batches: Incomplete
    stalled_until_ms: Incomplete

    def __init__(self, deployments: dict, nodes: dict | None=None) -> None:
        ...

    def row(self, namespace: str, name: str) -> dict:
        ...

    def deployment_object(self, key: str) -> OpaqueJson:
        ...

    def node_labels_of(self, node: str) -> OpaqueJson:
        ...

    def settle(self, key: str, ready: int | None=None) -> None:
        ...

def kube_memory(kube: KubeMemory) -> _Handler:
    ...
