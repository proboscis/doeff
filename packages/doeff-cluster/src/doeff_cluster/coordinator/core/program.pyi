# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = program.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from dataclasses import replace as replace
from doeff_cluster.shared.core.clock import now_epoch_ms as now_epoch_ms
from doeff_cluster.shared.intent.protocol import ClusterTiming as ClusterTiming
from doeff_cluster.shared.intent.protocol import Reply as Reply
from doeff_cluster.shared.intent.protocol import CoordinatorStopRequested as CoordinatorStopRequested
from doeff_cluster.shared.intent.protocol import Request as Request
from doeff_cluster.coordinator.intent.cluster_model import ClusterState as ClusterState
from doeff_cluster.coordinator.intent.cluster_model import ErrorReply as ErrorReply
from doeff_cluster.coordinator.intent.cluster_model import ClusterNaming as ClusterNaming
from doeff_cluster.coordinator.intent.cluster_model import IdleProbe as IdleProbe
from doeff_cluster.coordinator.intent.cluster_model import IdleNextRequests as IdleNextRequests
from doeff_cluster.coordinator.intent.cluster_model import IdleTaken as IdleTaken
from doeff_cluster.coordinator.intent.cluster_model import SaveState as SaveState
from doeff_cluster.coordinator.intent.cluster_model import Fault as Fault
from doeff_cluster.coordinator.intent.cluster_model import CoordinatorFault as CoordinatorFault
from doeff_cluster.coordinator.intent.cluster_model import Watcher as Watcher
from doeff_cluster.coordinator.intent.cluster_model import WatchRefusal as WatchRefusal
from doeff_cluster.coordinator.intent.cluster_model import WatchAnswer as WatchAnswer
from doeff_cluster.coordinator.intent.cluster_model import WatchStep as WatchStep
from doeff_cluster.coordinator.core.watch_policy import watch_of as watch_of
from doeff_cluster.coordinator.core.watch_policy import settle_watch as settle_watch
from doeff_cluster.coordinator.core.cluster_policy import nodes_to_read as nodes_to_read
from doeff_cluster.coordinator.core.cluster_policy import with_derived_capabilities as with_derived_capabilities
from doeff_cluster.coordinator.core.api_policy import respond as respond
from doeff_cluster.coordinator.core.api_policy import tick as tick
from doeff_cluster.coordinator.core.api_policy import plan_rollouts as plan_rollouts
from doeff_cluster.coordinator.core.api_policy import deployments_to_observe as deployments_to_observe
from doeff_cluster.coordinator.core.api_policy import scale_service as scale_service
from doeff_cluster.coordinator.core.api_policy import record_action as record_action
from doeff_cluster.coordinator.core.api_policy import mark_alive as mark_alive
from doeff_cluster.coordinator.core.api_policy import ROLLOUT_ACTOR as ROLLOUT_ACTOR
from doeff_cluster.coordinator.core.api_policy import ROLLOUT_TICK_MS as ROLLOUT_TICK_MS
from doeff_cluster.coordinator.core.api_policy import TICK_MS as TICK_MS
from doeff_cluster.coordinator.core.resource_policy import stamp as stamp
from doeff_cluster.coordinator.intent.request_bodies import ReadBody as ReadBody
from doeff_cluster.coordinator.intent.request_bodies import BodyUnreadable as BodyUnreadable
from doeff_cluster.coordinator.intent.kube_model import ScaleDeployment as ScaleDeployment
from doeff_cluster.coordinator.intent.kube_model import AnnotateDeployment as AnnotateDeployment
from doeff_cluster.coordinator.intent.kube_model import KubeUnavailable as KubeUnavailable
from doeff_cluster.coordinator.intent.kube_model import StartKubeReads as StartKubeReads
from doeff_cluster.coordinator.intent.kube_model import CollectKubeReads as CollectKubeReads
from doeff_cluster.coordinator.intent.kube_model import KubeReadsIdle as KubeReadsIdle
from doeff_cluster.coordinator.intent.kube_model import KubeReadsRunning as KubeReadsRunning
from doeff_cluster.coordinator.intent.kube_model import KubeReadsDone as KubeReadsDone
from doeff_core_effects.effects import slog as slog
KUBE_READS_NAMED_MS: int

def kube_observations(state: ClusterState, now: int) -> _Program[KubeReadsDone, object]:
    ...

def rollout_tick(state: ClusterState, timing: ClusterTiming, naming: ClusterNaming, now: int) -> _Program[ClusterState, object]:
    ...

def fault_reply(fault: Fault) -> _Program[ErrorReply, object]:
    ...

def readable_body(request: Request) -> _Program[Incomplete, object]:
    ...

def request_reply(state: ClusterState, request: Request, now: int, timing: ClusterTiming, settled: bool=False) -> _Program[tuple, object]:
    ...

def watch_answer_json(answer: WatchAnswer) -> _Program[dict, object]:
    ...

def coordinator_step(state: ClusterState, timing: ClusterTiming, naming: ClusterNaming, watchers: tuple) -> _Program[tuple, object]:
    ...

def release_watchers(state: ClusterState, watchers: tuple) -> _Program[int, object]:
    ...

def run_coordinator(state: ClusterState, timing: ClusterTiming, naming: ClusterNaming) -> _Program[ClusterState, object]:
    ...
