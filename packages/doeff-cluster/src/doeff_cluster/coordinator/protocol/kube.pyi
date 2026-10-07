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
from doeff_cluster.shared.core.clock import now_epoch_ms as now_epoch_ms
from doeff_cluster.shared.intent.protocol import NextRequests as NextRequests
from doeff_cluster.coordinator.intent.cluster_model import DeploymentReading as DeploymentReading
from doeff_cluster.coordinator.intent.cluster_model import DeploymentSeen as DeploymentSeen
from doeff_cluster.coordinator.intent.cluster_model import DeploymentUnreadable as DeploymentUnreadable
from doeff_cluster.coordinator.intent.cluster_model import NodeLabelsSeen as NodeLabelsSeen
from doeff_cluster.coordinator.intent.cluster_model import NodeLabelsUnreadable as NodeLabelsUnreadable
from doeff_cluster.coordinator.intent.kube_model import ScaleDeployment as ScaleDeployment
from doeff_cluster.coordinator.intent.kube_model import AnnotateDeployment as AnnotateDeployment
from doeff_cluster.coordinator.intent.kube_model import KubeUnavailable as KubeUnavailable
from doeff_cluster.coordinator.intent.kube_model import FollowDeployments as FollowDeployments
from doeff_cluster.coordinator.intent.kube_model import FollowNodes as FollowNodes
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler

def deployment_view(body: Mapping[str, object]) -> _Program[dict[str, object], object]:
    ...

def deployment_reading(view: Mapping[str, object]) -> _Program[DeploymentReading, object]:
    ...

def node_labels_view(body: Mapping[str, object]) -> _Program[Mapping[str, object] | None, object]:
    ...

def node_labels_table(labels: Mapping[str, object]) -> _Program[Table[str], object]:
    ...

class KubeCalls(Protocol):

    def follow(self, namespace: str, name: str, on_body: Callable[[OpaqueJson], None], on_error: Callable[[str], None]) -> Callable[[], None]:
        ...

    def follow_node(self, name: str, on_body: Callable[[OpaqueJson], None], on_error: Callable[[str], None]) -> Callable[[], None]:
        ...

    def scale(self, namespace: str, name: str, replicas: int, dry_run: bool) -> int:
        ...

    def annotate(self, namespace: str, name: str, annotations: dict) -> None:
        ...

class ObjectWatches:
    stops: Incomplete
    noted: Incomplete
    passed: Incomplete

    def __init__(self) -> None:
        ...

    @staticmethod
    def mark(seen: OpaqueJson | str) -> tuple:
        ...

    def note(self, key: str, seen: OpaqueJson | str) -> bool:
        ...

    def follow(self, keys: tuple[str, ...], start: Callable[[str], Callable[[], None]]) -> None:
        ...

    def take(self) -> tuple:
        ...

@dataclass(frozen=True, kw_only=True)
class KubeBodyRead:
    name: str
    body: OpaqueJson

@dataclass(frozen=True, kw_only=True)
class KubeReadFailed:
    name: str
    error: str

def read_of(key: str, seen: OpaqueJson | str) -> _Program[KubeBodyRead | KubeReadFailed, object]:
    ...

def deployment_observation_of(result: KubeBodyRead | KubeReadFailed, at: int) -> _Program[DeploymentSeen | DeploymentUnreadable, object]:
    ...

def deployment_writes(changes: tuple, at: int) -> _Program[tuple, object]:
    ...

def node_labels_observation_of(result: KubeBodyRead | KubeReadFailed, at: int) -> _Program[NodeLabelsSeen | NodeLabelsUnreadable, object]:
    ...

def node_writes(changes: tuple, at: int) -> _Program[tuple, object]:
    ...

def kube_api(client: KubeCalls, deployments: ObjectWatches, nodes: ObjectWatches, wake: Callable[[], None]) -> _Handler:
    ...

def kube_unavailable(reason: str, deployments: ObjectWatches, nodes: ObjectWatches) -> _Handler:
    ...

class KubeMemory:
    deployments: dict
    calls: Incomplete
    reads: Incomplete
    node_reads: Incomplete
    down: Incomplete
    nodes: Incomplete
    stalled_until_ms: Incomplete

    def __init__(self, deployments: dict, nodes: dict | None=None) -> None:
        ...

    def row(self, namespace: str, name: str) -> dict:
        ...

    def deployment_object(self, row: dict) -> OpaqueJson:
        ...

    def refusal_at(self, now_ms: int) -> str | None:
        ...

    def seen_at(self, key: str, now_ms: int) -> OpaqueJson | str:
        ...

    def node_seen_at(self, node: str, now_ms: int) -> OpaqueJson | str:
        ...

    def relabel(self, node: str, labels: dict) -> None:
        ...

    def settle(self, key: str, ready: int | None=None) -> None:
        ...

class MemoryWatch:
    following: Incomplete
    delivered: Incomplete

    def __init__(self) -> None:
        ...

    def changes(self, seen: Callable[[str], OpaqueJson | str]) -> tuple:
        ...

    def follow(self, seen: Callable[[str], OpaqueJson | str], keys: tuple[str, ...]) -> tuple:
        ...

class MemoryFollows:
    deployments: Incomplete
    nodes: Incomplete

    def __init__(self) -> None:
        ...

    def follow_deployments(self, kube: KubeMemory, keys: tuple[str, ...], now_ms: int) -> tuple:
        ...

    def follow_nodes(self, kube: KubeMemory, names: tuple[str, ...], now_ms: int) -> tuple:
        ...

    def changed(self, kube: KubeMemory, now_ms: int) -> bool:
        ...

    def change_at(self, kube: KubeMemory, now_ms: int) -> int | None:
        ...

def follow_wait(kube: KubeMemory, follows: MemoryFollows, timeout_seconds: float | None, now: int) -> _Program[float | None, object]:
    ...

def kube_memory(kube: KubeMemory, follows: MemoryFollows) -> _Handler:
    ...
