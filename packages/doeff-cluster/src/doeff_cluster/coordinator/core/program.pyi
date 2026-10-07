# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = program.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from datetime import datetime as datetime
from doeff_time import GetTime as GetTime
from doeff_time import epoch_ms_of as epoch_ms_of
from doeff_cluster.shared.core.clock import now_epoch_ms as now_epoch_ms
from doeff_cluster.shared.core.clock import datetime_of_epoch_ms as datetime_of_epoch_ms
from doeff_cluster.shared.intent.protocol import ClusterTiming as ClusterTiming
from doeff_cluster.shared.intent.protocol import NextRequests as NextRequests
from doeff_cluster.shared.intent.protocol import Reply as Reply
from doeff_cluster.shared.intent.protocol import CoordinatorStopRequested as CoordinatorStopRequested
from doeff_cluster.shared.intent.protocol import Request as Request
from doeff_cluster.coordinator.intent.cluster_model import ClusterState as ClusterState
from doeff_cluster.coordinator.intent.cluster_model import ErrorReply as ErrorReply
from doeff_cluster.coordinator.intent.cluster_model import ClusterNaming as ClusterNaming
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
from doeff_cluster.coordinator.core.api_policy import stamp_alive as stamp_alive
from doeff_cluster.coordinator.core.api_policy import ROLLOUT_ACTOR as ROLLOUT_ACTOR
from doeff_cluster.coordinator.core.api_policy import ROLLOUT_TICK_MS as ROLLOUT_TICK_MS
from doeff_cluster.coordinator.core.resource_policy import stamp as stamp
from doeff_cluster.coordinator.intent.request_bodies import ReadBody as ReadBody
from doeff_cluster.coordinator.intent.request_bodies import BodyUnreadable as BodyUnreadable
from doeff_cluster.coordinator.intent.request_bodies import HeartbeatBody as HeartbeatBody
from doeff_cluster.coordinator.intent.kube_model import ScaleDeployment as ScaleDeployment
from doeff_cluster.coordinator.intent.kube_model import AnnotateDeployment as AnnotateDeployment
from doeff_cluster.coordinator.intent.kube_model import KubeUnavailable as KubeUnavailable
from doeff_cluster.coordinator.intent.kube_model import StartKubeReads as StartKubeReads
from doeff_cluster.coordinator.intent.kube_model import CollectKubeReads as CollectKubeReads
from doeff_cluster.coordinator.intent.kube_model import KubeReadsIdle as KubeReadsIdle
from doeff_cluster.coordinator.intent.kube_model import KubeReadsRunning as KubeReadsRunning
from doeff_cluster.coordinator.intent.kube_model import KubeReadsDone as KubeReadsDone
from doeff_core_effects.effects import slog as slog
from doeff_core_effects.scheduler import Spawn as Spawn
from doeff_events import Publish as Publish
from doeff_events import NoticeSent as NoticeSent
from doeff_events import NoticeGapMarked as NoticeGapMarked
from doeff_events import NoticeDropped as NoticeDropped
from doeff_cluster.coordinator.core.cluster_policy import liveness_moves as liveness_moves
from doeff_cluster.coordinator.core.cluster_policy import liveness_now as liveness_now
from doeff_cluster.coordinator.core.cluster_policy import note_liveness as note_liveness
from doeff_cluster.shared.intent.due_model import DueAt as DueAt
from doeff_cluster.shared.intent.due_model import DueNow as DueNow
from doeff_cluster.shared.intent.due_model import DueNever as DueNever
from doeff_cluster.coordinator.core.wake_policy import next_wake as next_wake
from doeff_cluster.coordinator.core.wake_policy import after_step as after_step
from doeff_cluster.coordinator.core.wake_policy import wait_seconds as wait_seconds
from doeff_cluster.coordinator.core.wake_policy import count_unsettled as count_unsettled
KUBE_READS_NAMED_MS: int
HEARTBEAT_LAG_LOG: str
HEARTBEAT_LAG_MS: int

@dataclass(frozen=True, kw_only=True)
class StepMarks:
    taken: int
    judged: int
    rolled: int
    saved: int
    replied: int

@dataclass(frozen=True, kw_only=True)
class LagSpan:
    name: str
    ms: int

@dataclass(frozen=True, kw_only=True)
class HeardBeat:
    worker: str
    queued_ms: int

@dataclass(frozen=True, kw_only=True)
class HeartbeatLag:
    elapsed_ms: int
    slowest: str
    slowest_ms: int

def heartbeat_lag(marks: StepMarks, queued_ms: int) -> _Program[HeartbeatLag, object]:
    ...

def lap_ms(measuring: bool) -> _Program[int, object]:
    ...

def note_heartbeat_lags(heard: tuple, marks: StepMarks) -> _Program[int, object]:
    ...

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

def announce_liveness(events: tuple) -> _Program[int, object]:
    ...

def announced_aside(events: tuple) -> _Program[None, object]:
    ...

def coordinator_step(state: ClusterState, timing: ClusterTiming, naming: ClusterNaming, watchers: tuple, wait: float | None) -> _Program[tuple, object]:
    ...

def release_watchers(state: ClusterState, watchers: tuple) -> _Program[int, object]:
    ...

def run_coordinator(state: ClusterState, timing: ClusterTiming, naming: ClusterNaming) -> _Program[ClusterState, object]:
    ...
