"""doeff_cluster: the effects and handlers of the package (and their types), read on first use."""

from importlib import import_module
from typing import TYPE_CHECKING

# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`
# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).
if TYPE_CHECKING:
    from doeff_cluster._sim.checkout_git_script import GitCheckout as GitCheckout
    from doeff_cluster._sim.checkout_git_script import GitRemote as GitRemote
    from doeff_cluster._sim.checkout_git_script import GitRev as GitRev
    from doeff_cluster._sim.checkout_git_script import git_command as git_command
    from doeff_cluster._sim.k8s_services import held_kube_services as held_kube_services
    from doeff_cluster._sim.k8s_supplied import OBJECT_PATH as OBJECT_PATH
    from doeff_cluster._sim.k8s_supplied import HeldEntry as HeldEntry
    from doeff_cluster._sim.k8s_supplied import HeldObject as HeldObject
    from doeff_cluster._sim.k8s_supplied import ReadGrant as ReadGrant
    from doeff_cluster._sim.k8s_supplied import SuppliedCluster as SuppliedCluster
    from doeff_cluster._sim.k8s_supplied import answer_of as answer_of
    from doeff_cluster._sim.k8s_supplied import held_value_of as held_value_of
    from doeff_cluster._sim.k8s_supplied import held_whole_of as held_whole_of
    from doeff_cluster._sim.k8s_supplied import k8s_supplied_peer as k8s_supplied_peer
    from doeff_cluster._sim.k8s_supplied import read_grant_of as read_grant_of
    from doeff_cluster._sim.k8s_supplied import (
        service_account_token_file as service_account_token_file,
    )
    from doeff_cluster._sim.local import SIM_START_MS as SIM_START_MS
    from doeff_cluster._sim.local import SIM_URL as SIM_URL
    from doeff_cluster._sim.local import AdmitBatch as AdmitBatch
    from doeff_cluster._sim.local import ArmWaitChange as ArmWaitChange
    from doeff_cluster._sim.local import AwaitProcessStarted as AwaitProcessStarted
    from doeff_cluster._sim.local import ChangeHostTruth as ChangeHostTruth
    from doeff_cluster._sim.local import ClientLink as ClientLink
    from doeff_cluster._sim.local import CoordinatorEnded as CoordinatorEnded
    from doeff_cluster._sim.local import CoordinatorEnvironOf as CoordinatorEnvironOf
    from doeff_cluster._sim.local import CoordinatorRuns as CoordinatorRuns
    from doeff_cluster._sim.local import CoordinatorStarted as CoordinatorStarted
    from doeff_cluster._sim.local import CoordinatorSteps as CoordinatorSteps
    from doeff_cluster._sim.local import CutWorker as CutWorker
    from doeff_cluster._sim.local import DeclareRollout as DeclareRollout
    from doeff_cluster._sim.local import DowntimeOf as DowntimeOf
    from doeff_cluster._sim.local import DrainGeneration as DrainGeneration
    from doeff_cluster._sim.local import DrainWorker as DrainWorker
    from doeff_cluster._sim.local import EndProcess as EndProcess
    from doeff_cluster._sim.local import FailRoute as FailRoute
    from doeff_cluster._sim.local import HandleOf as HandleOf
    from doeff_cluster._sim.local import HostTruthOf as HostTruthOf
    from doeff_cluster._sim.local import KeepChild as KeepChild
    from doeff_cluster._sim.local import KeepHandle as KeepHandle
    from doeff_cluster._sim.local import KubeCalls as KubeCalls
    from doeff_cluster._sim.local import KubeReads as KubeReads
    from doeff_cluster._sim.local import NextPid as NextPid
    from doeff_cluster._sim.local import NextWorldDue as NextWorldDue
    from doeff_cluster._sim.local import NodeReads as NodeReads
    from doeff_cluster._sim.local import NoteCoordinatorWrite as NoteCoordinatorWrite
    from doeff_cluster._sim.local import NoteEventWait as NoteEventWait
    from doeff_cluster._sim.local import NotePreparation as NotePreparation
    from doeff_cluster._sim.local import NoteProcess as NoteProcess
    from doeff_cluster._sim.local import NoteScenarioWait as NoteScenarioWait
    from doeff_cluster._sim.local import NoteWatchFailure as NoteWatchFailure
    from doeff_cluster._sim.local import PartsOf as PartsOf
    from doeff_cluster._sim.local import PauseDue as PauseDue
    from doeff_cluster._sim.local import PersistCrashDue as PersistCrashDue
    from doeff_cluster._sim.local import PlanOf as PlanOf
    from doeff_cluster._sim.local import PreparationsOf as PreparationsOf
    from doeff_cluster._sim.local import ProcessesOf as ProcessesOf
    from doeff_cluster._sim.local import ProcessNoticeRaised as ProcessNoticeRaised
    from doeff_cluster._sim.local import ProcessNoticeWait as ProcessNoticeWait
    from doeff_cluster._sim.local import ProcessOutside as ProcessOutside
    from doeff_cluster._sim.local import ProcessStopAsked as ProcessStopAsked
    from doeff_cluster._sim.local import ProcessStopRaised as ProcessStopRaised
    from doeff_cluster._sim.local import ProcessStopWait as ProcessStopWait
    from doeff_cluster._sim.local import PutHostTruth as PutHostTruth
    from doeff_cluster._sim.local import ReadCoordinator as ReadCoordinator
    from doeff_cluster._sim.local import RelabelNode as RelabelNode
    from doeff_cluster._sim.local import ReleaseRequest as ReleaseRequest
    from doeff_cluster._sim.local import ReplaceCoordinatorEnviron as ReplaceCoordinatorEnviron
    from doeff_cluster._sim.local import ReplaceWorker as ReplaceWorker
    from doeff_cluster._sim.local import ReportsOf as ReportsOf
    from doeff_cluster._sim.local import RevivalOf as RevivalOf
    from doeff_cluster._sim.local import RollBellOf as RollBellOf
    from doeff_cluster._sim.local import RollWorker as RollWorker
    from doeff_cluster._sim.local import RoomFileText as RoomFileText
    from doeff_cluster._sim.local import SettleDeployment as SettleDeployment
    from doeff_cluster._sim.local import SharedRows as SharedRows
    from doeff_cluster._sim.local import SimDeadlockError as SimDeadlockError
    from doeff_cluster._sim.local import SimLivenessError as SimLivenessError
    from doeff_cluster._sim.local import SimOutside as SimOutside
    from doeff_cluster._sim.local import SimPreparation as SimPreparation
    from doeff_cluster._sim.local import SimProcess as SimProcess
    from doeff_cluster._sim.local import SimReport as SimReport
    from doeff_cluster._sim.local import SimWorker as SimWorker
    from doeff_cluster._sim.local import StallCoordinator as StallCoordinator
    from doeff_cluster._sim.local import StallWorker as StallWorker
    from doeff_cluster._sim.local import StartWorker as StartWorker
    from doeff_cluster._sim.local import StepEnded as StepEnded
    from doeff_cluster._sim.local import StepWorkerWall as StepWorkerWall
    from doeff_cluster._sim.local import StopElder as StopElder
    from doeff_cluster._sim.local import StopRequestOf as StopRequestOf
    from doeff_cluster._sim.local import StopWorkers as StopWorkers
    from doeff_cluster._sim.local import TakeHeldRequests as TakeHeldRequests
    from doeff_cluster._sim.local import TrackedSpawn as TrackedSpawn
    from doeff_cluster._sim.local import WaitsOf as WaitsOf
    from doeff_cluster._sim.local import WallBackOf as WallBackOf
    from doeff_cluster._sim.local import WatchFailuresOf as WatchFailuresOf
    from doeff_cluster._sim.local import WorkerDied as WorkerDied
    from doeff_cluster._sim.local import WorkerEnded as WorkerEnded
    from doeff_cluster._sim.local import WorkerOf as WorkerOf
    from doeff_cluster._sim.local import WorkersStopping as WorkersStopping
    from doeff_cluster._sim.local import WriteRoomFile as WriteRoomFile
    from doeff_cluster._sim.local import business_wait_tap as business_wait_tap
    from doeff_cluster._sim.local import coordinator_answers as coordinator_answers
    from doeff_cluster._sim.local import fence as fence
    from doeff_cluster._sim.local import heartbeat as heartbeat
    from doeff_cluster._sim.local import host_answers as host_answers
    from doeff_cluster._sim.local import no_business_timers as no_business_timers
    from doeff_cluster._sim.local import observe_requests as observe_requests
    from doeff_cluster._sim.local import process_notices as process_notices
    from doeff_cluster._sim.local import process_signals as process_signals
    from doeff_cluster._sim.local import scenario_wait_tap as scenario_wait_tap
    from doeff_cluster._sim.local import sim_cluster as sim_cluster
    from doeff_cluster._sim.local import sim_host as sim_host
    from doeff_cluster._sim.local import sim_world as sim_world
    from doeff_cluster._sim.local import wall_sim_cluster as wall_sim_cluster
    from doeff_cluster._sim.local import worker_wall as worker_wall
    from doeff_cluster._sim.machine import GitSource as GitSource
    from doeff_cluster._sim.machine import LocalMachine as LocalMachine
    from doeff_cluster._sim.machine import MachineCannotAnswer as MachineCannotAnswer
    from doeff_cluster._sim.machine import local_machine_cluster as local_machine_cluster
    from doeff_cluster._sim.machine import machine_answers as machine_answers
    from doeff_cluster._sim.machine import stopped as stopped
    from doeff_cluster._sim.worktree_git_script import OriginBranch as OriginBranch
    from doeff_cluster._sim.worktree_git_script import sha_of as sha_of
    from doeff_cluster._sim.worktree_git_script import worktree_git_answer as worktree_git_answer
    from doeff_cluster._sim.worktree_git_script import worktree_git_command as worktree_git_command
    from doeff_cluster._coordinator.core.program import run_coordinator as run_coordinator
    from doeff_cluster._coordinator.entry.main import load_state as load_state
    from doeff_cluster._coordinator.protocol.store import Persist as Persist
    from doeff_cluster._foundation.host_contract import environ_table_reader as environ_table_reader
    from doeff_cluster._foundation.process_versions import process_versions as process_versions
    from doeff_cluster.shared.core.clock import now_epoch_ms as now_epoch_ms
    from doeff_cluster.shared.core.run_context_rules import (
        runtime_env_of_context as runtime_env_of_context,
    )
    from doeff_cluster.shared.intent.cluster_control import AwaitJobProcess as AwaitJobProcess
    from doeff_cluster.shared.intent.cluster_control import AwaitReadiness as AwaitReadiness
    from doeff_cluster.shared.intent.cluster_control import Crash as Crash
    from doeff_cluster.shared.intent.cluster_control import CrashCoordinator as CrashCoordinator
    from doeff_cluster.shared.intent.cluster_control import KillWorker as KillWorker
    from doeff_cluster.shared.intent.cluster_control import ReadinessOf as ReadinessOf
    from doeff_cluster.shared.intent.cluster_control import Redeclare as Redeclare
    from doeff_cluster.shared.intent.cluster_control import RestartService as RestartService
    from doeff_cluster.shared.intent.cluster_control import StopCoordinator as StopCoordinator
    from doeff_cluster.shared.intent.cluster_control import StopWorker as StopWorker
    from doeff_cluster.shared.intent.detached_model import AckDetachedOutbox as AckDetachedOutbox
    from doeff_cluster.shared.intent.detached_model import AwaitDetached as AwaitDetached
    from doeff_cluster.shared.intent.detached_model import (
        AwaitDetachedOutbox as AwaitDetachedOutbox,
    )
    from doeff_cluster.shared.intent.detached_model import AwaitRunnersChange as AwaitRunnersChange
    from doeff_cluster.shared.intent.detached_model import AwaitServiceReady as AwaitServiceReady
    from doeff_cluster.shared.intent.detached_model import CancelDetached as CancelDetached
    from doeff_cluster.shared.intent.detached_model import ReadDetachedQueue as ReadDetachedQueue
    from doeff_cluster.shared.intent.detached_model import ReadDetachedTiming as ReadDetachedTiming
    from doeff_cluster.shared.intent.detached_model import ReadRunners as ReadRunners
    from doeff_cluster.shared.intent.detached_model import ReadServices as ReadServices
    from doeff_cluster.shared.intent.detached_model import ReleaseDetached as ReleaseDetached
    from doeff_cluster.shared.intent.detached_model import SubmitDetached as SubmitDetached
    from doeff_cluster.shared.intent.kube_service_model import (
        EnsureKubeIngress as EnsureKubeIngress,
    )
    from doeff_cluster.shared.intent.kube_service_model import (
        EnsureKubeService as EnsureKubeService,
    )
    from doeff_cluster.shared.intent.metrics_model import ReportMetrics as ReportMetrics
    from doeff_cluster.shared.intent.process_model import AwaitProcessEnded as AwaitProcessEnded
    from doeff_cluster.shared.intent.protocol import (
        CoordinatorStopRequested as CoordinatorStopRequested,
    )
    from doeff_cluster.shared.intent.protocol import NextRequests as NextRequests
    from doeff_cluster.shared.intent.protocol import Reply as Reply
    from doeff_cluster.shared.intent.readiness_model import ReportReady as ReportReady
    from doeff_cluster.shared.intent.remote_model import RemoteJob as RemoteJob
    from doeff_cluster.shared.intent.remote_model import RemoteJobFailed as RemoteJobFailed
    from doeff_cluster.shared.intent.semaphore_model import AwaitLeaseFree as AwaitLeaseFree
    from doeff_cluster.shared.intent.semaphore_model import AwaitLeaseVacated as AwaitLeaseVacated
    from doeff_cluster.shared.intent.semaphore_model import LeaseOp as LeaseOp
    from doeff_cluster.shared.intent.shared_model import ReadShared as ReadShared
    from doeff_cluster.shared.intent.shared_model import WriteShared as WriteShared
    from doeff_cluster.shared.intent.warm_model import AwaitWarm as AwaitWarm
    from doeff_cluster.shared.intent.warm_model import ReadWarmState as ReadWarmState
    from doeff_cluster.shared.intent.warm_model import WarmRuntimeEnv as WarmRuntimeEnv
    from doeff_cluster.shared.protocol.coordinator_reads import (
        readiness_awaited as readiness_awaited,
    )
    from doeff_cluster.shared.protocol.inbox import http_request as http_request
    from doeff_cluster.worker.intent.retirement_model import AwaitRetirement as AwaitRetirement
    from doeff_cluster.worker.intent.worker_model import AwaitNextTick as AwaitNextTick
    from doeff_cluster.worker.intent.worker_model import EnvReport as EnvReport
    from doeff_cluster.worker.intent.worker_model import ForgetProbes as ForgetProbes
    from doeff_cluster.worker.intent.worker_model import ForgetWarmChild as ForgetWarmChild
    from doeff_cluster.worker.intent.worker_model import NoticeJob as NoticeJob
    from doeff_cluster.worker.intent.worker_model import ObserveWorld as ObserveWorld
    from doeff_cluster.worker.intent.worker_model import PrepareCode as PrepareCode
    from doeff_cluster.worker.intent.worker_model import PrepareEnv as PrepareEnv
    from doeff_cluster.worker.intent.worker_model import ProbeEntry as ProbeEntry
    from doeff_cluster.worker.intent.worker_model import PublishStatus as PublishStatus
    from doeff_cluster.worker.intent.worker_model import ReadDesired as ReadDesired
    from doeff_cluster.worker.intent.worker_model import ReapJob as ReapJob
    from doeff_cluster.worker.intent.worker_model import ReleaseLeases as ReleaseLeases
    from doeff_cluster.worker.intent.worker_model import RetireJob as RetireJob
    from doeff_cluster.worker.intent.worker_model import SignalJob as SignalJob
    from doeff_cluster.worker.intent.worker_model import StartJob as StartJob
    from doeff_cluster.worker.intent.worker_model import StartWarmChild as StartWarmChild
    from doeff_cluster.worker.intent.worker_model import StopWarmChild as StopWarmChild
    from doeff_cluster.worker.intent.worker_model import SweepEnvs as SweepEnvs
    from doeff_cluster.worker.intent.worker_model import WorkerWakes as WorkerWakes
    from doeff_cluster.worker.protocol.declared import declared_job_specs as declared_job_specs
    from doeff_cluster.worker.protocol.declared import (
        declared_reply_of_json as declared_reply_of_json,
    )
    from doeff_cluster.shared.protocol.inbox import http_requests as http_requests
    from doeff_cluster.shared.protocol.inbox import stop_flag as stop_flag
    from doeff_cluster._foundation.in_cluster_api import CLUSTER_API as CLUSTER_API
    from doeff_cluster._foundation.in_cluster_api import ClusterCaMissing as ClusterCaMissing
    from doeff_cluster._foundation.record_codec import DECISION as DECISION
    from doeff_cluster._foundation.record_codec import EffectCodec as EffectCodec
    from doeff_cluster._foundation.foundation_check import FoundationClosure as FoundationClosure
    from doeff_cluster._foundation.host_contract import HOST_CONTRACT as HOST_CONTRACT
    from doeff_cluster._foundation.record_codec import MalformedRecordSpec as MalformedRecordSpec
    from doeff_cluster._foundation.record_codec import READ as READ
    from doeff_cluster._foundation.record_codec import RecordedError as RecordedError
    from doeff_cluster._foundation.in_cluster_api import (
        SERVICE_ACCOUNT_CA_PATH as SERVICE_ACCOUNT_CA_PATH,
    )
    from doeff_cluster._foundation.host_contract import SIM_PASSABLE as SIM_PASSABLE
    from doeff_cluster._foundation.process_versions import SourceUnidentified as SourceUnidentified
    from doeff_cluster._foundation.record_codec import UnencodableValue as UnencodableValue
    from doeff_cluster._foundation.foundation_check import UnreadDeclaration as UnreadDeclaration
    from doeff_cluster._foundation.record_codec import UnrecordableEffect as UnrecordableEffect
    from doeff_cluster._foundation.wal_store import WalStore as WalStore
    from doeff_cluster._foundation.foundation_check import foundation_closure as foundation_closure
    from doeff_cluster._foundation.foundation_check import (
        hyx_closedXquestion_markX as hyx_closedXquestion_markX,
    )
    from doeff_cluster._foundation.in_cluster_api import (
        in_cluster_api_connected as in_cluster_api_connected,
    )
    from doeff_cluster._foundation.in_cluster_api import (
        in_cluster_api_routed as in_cluster_api_routed,
    )
    from doeff_cluster._foundation.kube_client import kube_headers as kube_headers
    from doeff_cluster._foundation.host_contract import os_environ_reader as os_environ_reader
    from doeff_cluster._foundation.record_codec import register as register
    from doeff_cluster._foundation.record_codec import registered_types as registered_types
    from doeff_cluster._foundation.record_codec import type_name as type_name
    from doeff_cluster._coordinator.core.api_policy import ALIVE_MARK_MS as ALIVE_MARK_MS
    from doeff_cluster._coordinator.intent.kube_model import (
        AnnotateDeployment as AnnotateDeployment,
    )
    from doeff_cluster._coordinator.intent.cluster_model import BoardRow as BoardRow
    from doeff_cluster.shared.intent.protocol import BodyInvalid as BodyInvalid
    from doeff_cluster._coordinator.intent.cluster_model import ClusterJob as ClusterJob
    from doeff_cluster._coordinator.intent.cluster_model import ClusterNaming as ClusterNaming
    from doeff_cluster._coordinator.intent.cluster_model import ClusterState as ClusterState
    from doeff_cluster._coordinator.intent.cluster_model import CoordinatorFault as CoordinatorFault
    from doeff_cluster._coordinator.intent.due_model import (
        CoordinatorUnsettled as CoordinatorUnsettled,
    )
    from doeff_cluster._coordinator.intent.failover_model import ExtraProvides as ExtraProvides
    from doeff_cluster._coordinator.intent.failover_model import FailoverWorker as FailoverWorker
    from doeff_cluster._coordinator.intent.kube_model import FollowDeployments as FollowDeployments
    from doeff_cluster._coordinator.intent.kube_model import FollowNodes as FollowNodes
    from doeff_cluster._coordinator.core.cluster_policy import (
        IMAGE_FOLLOW_KEYS as IMAGE_FOLLOW_KEYS,
    )
    from doeff_cluster._coordinator.intent.failover_model import KeptApart as KeptApart
    from doeff_cluster._coordinator.intent.kube_model import KubeUnavailable as KubeUnavailable
    from doeff_cluster._coordinator.intent.failover_model import ResidentJob as ResidentJob
    from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnvInvalid as RuntimeEnvInvalid
    from doeff_cluster._coordinator.intent.cluster_model import SaveState as SaveState
    from doeff_cluster._coordinator.intent.kube_model import ScaleDeployment as ScaleDeployment
    from doeff_cluster.shared.intent.protocol import ServiceReached as ServiceReached
    from doeff_cluster._coordinator.intent.cluster_model import UnplacedKind as UnplacedKind
    from doeff_cluster._coordinator.protocol.worker_notices import (
        WORKER_NOTICE_READS as WORKER_NOTICE_READS,
    )
    from doeff_cluster._coordinator.protocol.worker_notices import (
        WORKER_NOTICE_ROUTES as WORKER_NOTICE_ROUTES,
    )
    from doeff_cluster._coordinator.intent.worker_notices import WorkerBack as WorkerBack
    from doeff_cluster._coordinator.intent.worker_notices import WorkerGone as WorkerGone
    from doeff_cluster._coordinator.intent.cluster_model import WorkerInfo as WorkerInfo
    from doeff_cluster._coordinator.intent.cluster_model import WorkerLoad as WorkerLoad
    from doeff_cluster._coordinator.protocol.store import durable_states as durable_states
    from doeff_cluster.shared.core.runtime_env_rules import env_key as env_key
    from doeff_cluster._coordinator.core.failover_policy import (
        failover_refusals as failover_refusals,
    )
    from doeff_cluster._coordinator.core.failover_policy import (
        hyx_per_machineXquestion_markX as hyx_per_machineXquestion_markX,
    )
    from doeff_cluster._coordinator.protocol.kube import kube_api as kube_api
    from doeff_cluster._coordinator.protocol.kube import kube_memory as kube_memory
    from doeff_cluster._coordinator.protocol.kube import kube_unavailable as kube_unavailable
    from doeff_cluster._coordinator.core.cluster_policy import liveness_due as liveness_due
    from doeff_cluster._coordinator.core.cluster_policy import memory_misfit as memory_misfit
    from doeff_cluster._coordinator.core.cluster_policy import memory_misfits as memory_misfits
    from doeff_cluster._coordinator.core.cluster_policy import memory_room_of as memory_room_of
    from doeff_cluster._coordinator.protocol.store import memory_store as memory_store
    from doeff_cluster._coordinator.protocol.cluster_json import (
        naming_from_json as naming_from_json,
    )
    from doeff_cluster._coordinator.core.cluster_policy import placeable as placeable
    from doeff_cluster._coordinator.protocol.request_bodies import request_bodies as request_bodies
    from doeff_cluster._coordinator.protocol.request_bodies import responded as responded
    from doeff_cluster._coordinator.core.failover_room import room_file_workers as room_file_workers
    from doeff_cluster.shared.core.runtime_env_rules import (
        runtime_env_of_json as runtime_env_of_json,
    )
    from doeff_cluster._coordinator.core.cluster_policy import (
        spec_of_declaration as spec_of_declaration,
    )
    from doeff_cluster._coordinator.core.cluster_policy import sweep_due as sweep_due
    from doeff_cluster._coordinator.core.cluster_policy import task_due as task_due
    from doeff_cluster._coordinator.core.api_policy import tick as tick
    from doeff_cluster._coordinator.core.api_policy import tick_due as tick_due
    from doeff_cluster._coordinator.core.cluster_policy import unplaced_kind as unplaced_kind
    from doeff_cluster._coordinator.core.cluster_policy import unplaced_memory as unplaced_memory
    from doeff_cluster._coordinator.protocol.store import wal_store as wal_store
    from doeff_cluster.shared.core.warm_rules import warm_key as warm_key

_LAZY_EXPORTS: dict[str, str] = {
    "AckDetachedOutbox": "doeff_cluster.shared.intent.detached_model",
    "AdmitBatch": "doeff_cluster._sim.local",
    "ArmWaitChange": "doeff_cluster._sim.local",
    "AwaitDetached": "doeff_cluster.shared.intent.detached_model",
    "AwaitDetachedOutbox": "doeff_cluster.shared.intent.detached_model",
    "AwaitJobProcess": "doeff_cluster.shared.intent.cluster_control",
    "AwaitLeaseFree": "doeff_cluster.shared.intent.semaphore_model",
    "AwaitLeaseVacated": "doeff_cluster.shared.intent.semaphore_model",
    "AwaitNextTick": "doeff_cluster.worker.intent.worker_model",
    "AwaitProcessEnded": "doeff_cluster.shared.intent.process_model",
    "AwaitProcessStarted": "doeff_cluster._sim.local",
    "AwaitReadiness": "doeff_cluster.shared.intent.cluster_control",
    "AwaitRetirement": "doeff_cluster.worker.intent.retirement_model",
    "AwaitRunnersChange": "doeff_cluster.shared.intent.detached_model",
    "AwaitServiceReady": "doeff_cluster.shared.intent.detached_model",
    "AwaitWarm": "doeff_cluster.shared.intent.warm_model",
    "CancelDetached": "doeff_cluster.shared.intent.detached_model",
    "ChangeHostTruth": "doeff_cluster._sim.local",
    "ClientLink": "doeff_cluster._sim.local",
    "CoordinatorEnded": "doeff_cluster._sim.local",
    "CoordinatorEnvironOf": "doeff_cluster._sim.local",
    "CoordinatorRuns": "doeff_cluster._sim.local",
    "CoordinatorStarted": "doeff_cluster._sim.local",
    "CoordinatorSteps": "doeff_cluster._sim.local",
    "CoordinatorStopRequested": "doeff_cluster.shared.intent.protocol",
    "Crash": "doeff_cluster.shared.intent.cluster_control",
    "CrashCoordinator": "doeff_cluster.shared.intent.cluster_control",
    "CutWorker": "doeff_cluster._sim.local",
    "DeclareRollout": "doeff_cluster._sim.local",
    "DowntimeOf": "doeff_cluster._sim.local",
    "DrainGeneration": "doeff_cluster._sim.local",
    "DrainWorker": "doeff_cluster._sim.local",
    "EndProcess": "doeff_cluster._sim.local",
    "EnsureKubeIngress": "doeff_cluster.shared.intent.kube_service_model",
    "EnsureKubeService": "doeff_cluster.shared.intent.kube_service_model",
    "EnvReport": "doeff_cluster.worker.intent.worker_model",
    "FailRoute": "doeff_cluster._sim.local",
    "ForgetProbes": "doeff_cluster.worker.intent.worker_model",
    "ForgetWarmChild": "doeff_cluster.worker.intent.worker_model",
    "GitCheckout": "doeff_cluster._sim.checkout_git_script",
    "GitRemote": "doeff_cluster._sim.checkout_git_script",
    "GitRev": "doeff_cluster._sim.checkout_git_script",
    "GitSource": "doeff_cluster._sim.machine",
    "HandleOf": "doeff_cluster._sim.local",
    "HeldEntry": "doeff_cluster._sim.k8s_supplied",
    "HeldObject": "doeff_cluster._sim.k8s_supplied",
    "HostTruthOf": "doeff_cluster._sim.local",
    "KeepChild": "doeff_cluster._sim.local",
    "KeepHandle": "doeff_cluster._sim.local",
    "KillWorker": "doeff_cluster.shared.intent.cluster_control",
    "KubeCalls": "doeff_cluster._sim.local",
    "KubeReads": "doeff_cluster._sim.local",
    "LeaseOp": "doeff_cluster.shared.intent.semaphore_model",
    "LocalMachine": "doeff_cluster._sim.machine",
    "MachineCannotAnswer": "doeff_cluster._sim.machine",
    "NextPid": "doeff_cluster._sim.local",
    "NextRequests": "doeff_cluster.shared.intent.protocol",
    "NextWorldDue": "doeff_cluster._sim.local",
    "NodeReads": "doeff_cluster._sim.local",
    "NoteCoordinatorWrite": "doeff_cluster._sim.local",
    "NoteEventWait": "doeff_cluster._sim.local",
    "NotePreparation": "doeff_cluster._sim.local",
    "NoteProcess": "doeff_cluster._sim.local",
    "NoteScenarioWait": "doeff_cluster._sim.local",
    "NoteWatchFailure": "doeff_cluster._sim.local",
    "NoticeJob": "doeff_cluster.worker.intent.worker_model",
    "OBJECT_PATH": "doeff_cluster._sim.k8s_supplied",
    "ObserveWorld": "doeff_cluster.worker.intent.worker_model",
    "OriginBranch": "doeff_cluster._sim.worktree_git_script",
    "PartsOf": "doeff_cluster._sim.local",
    "PauseDue": "doeff_cluster._sim.local",
    "Persist": "doeff_cluster._coordinator.protocol.store",
    "PersistCrashDue": "doeff_cluster._sim.local",
    "PlanOf": "doeff_cluster._sim.local",
    "PreparationsOf": "doeff_cluster._sim.local",
    "PrepareCode": "doeff_cluster.worker.intent.worker_model",
    "PrepareEnv": "doeff_cluster.worker.intent.worker_model",
    "ProbeEntry": "doeff_cluster.worker.intent.worker_model",
    "ProcessNoticeRaised": "doeff_cluster._sim.local",
    "ProcessNoticeWait": "doeff_cluster._sim.local",
    "ProcessOutside": "doeff_cluster._sim.local",
    "ProcessStopAsked": "doeff_cluster._sim.local",
    "ProcessStopRaised": "doeff_cluster._sim.local",
    "ProcessStopWait": "doeff_cluster._sim.local",
    "ProcessesOf": "doeff_cluster._sim.local",
    "PublishStatus": "doeff_cluster.worker.intent.worker_model",
    "PutHostTruth": "doeff_cluster._sim.local",
    "ReadCoordinator": "doeff_cluster._sim.local",
    "ReadDesired": "doeff_cluster.worker.intent.worker_model",
    "ReadDetachedQueue": "doeff_cluster.shared.intent.detached_model",
    "ReadDetachedTiming": "doeff_cluster.shared.intent.detached_model",
    "ReadGrant": "doeff_cluster._sim.k8s_supplied",
    "ReadRunners": "doeff_cluster.shared.intent.detached_model",
    "ReadServices": "doeff_cluster.shared.intent.detached_model",
    "ReadShared": "doeff_cluster.shared.intent.shared_model",
    "ReadWarmState": "doeff_cluster.shared.intent.warm_model",
    "ReadinessOf": "doeff_cluster.shared.intent.cluster_control",
    "ReapJob": "doeff_cluster.worker.intent.worker_model",
    "ReceivedLines": "doeff_cluster._sim.test_recording_on_sim",
    "Redeclare": "doeff_cluster.shared.intent.cluster_control",
    "RelabelNode": "doeff_cluster._sim.local",
    "ReleaseDetached": "doeff_cluster.shared.intent.detached_model",
    "ReleaseLeases": "doeff_cluster.worker.intent.worker_model",
    "ReleaseRequest": "doeff_cluster._sim.local",
    "RemoteJob": "doeff_cluster.shared.intent.remote_model",
    "RemoteJobFailed": "doeff_cluster.shared.intent.remote_model",
    "ReplaceCoordinatorEnviron": "doeff_cluster._sim.local",
    "ReplaceWorker": "doeff_cluster._sim.local",
    "ReplayDiverged": "doeff_cluster._foundation.record_log",
    "Reply": "doeff_cluster.shared.intent.protocol",
    "ReportMetrics": "doeff_cluster.shared.intent.metrics_model",
    "ReportReady": "doeff_cluster.shared.intent.readiness_model",
    "ReportsOf": "doeff_cluster._sim.local",
    "RestartService": "doeff_cluster.shared.intent.cluster_control",
    "RetireJob": "doeff_cluster.worker.intent.worker_model",
    "RevivalOf": "doeff_cluster._sim.local",
    "RollBellOf": "doeff_cluster._sim.local",
    "RollWorker": "doeff_cluster._sim.local",
    "RoomFileText": "doeff_cluster._sim.local",
    "SIM_START_MS": "doeff_cluster._sim.local",
    "SIM_URL": "doeff_cluster._sim.local",
    "SettleDeployment": "doeff_cluster._sim.local",
    "SharedRows": "doeff_cluster._sim.local",
    "SignalJob": "doeff_cluster.worker.intent.worker_model",
    "SimDeadlockError": "doeff_cluster._sim.local",
    "SimLivenessError": "doeff_cluster._sim.local",
    "SimOutside": "doeff_cluster._sim.local",
    "SimPreparation": "doeff_cluster._sim.local",
    "SimProcess": "doeff_cluster._sim.local",
    "SimReport": "doeff_cluster._sim.local",
    "SimWorker": "doeff_cluster._sim.local",
    "StallCoordinator": "doeff_cluster._sim.local",
    "StallWorker": "doeff_cluster._sim.local",
    "StartJob": "doeff_cluster.worker.intent.worker_model",
    "StartWarmChild": "doeff_cluster.worker.intent.worker_model",
    "StartWorker": "doeff_cluster._sim.local",
    "StepEnded": "doeff_cluster._sim.local",
    "StepWorkerWall": "doeff_cluster._sim.local",
    "StopCoordinator": "doeff_cluster.shared.intent.cluster_control",
    "StopElder": "doeff_cluster._sim.local",
    "StopRequestOf": "doeff_cluster._sim.local",
    "StopWarmChild": "doeff_cluster.worker.intent.worker_model",
    "StopWorker": "doeff_cluster.shared.intent.cluster_control",
    "StopWorkers": "doeff_cluster._sim.local",
    "SubmitDetached": "doeff_cluster.shared.intent.detached_model",
    "SuppliedCluster": "doeff_cluster._sim.k8s_supplied",
    "SweepEnvs": "doeff_cluster.worker.intent.worker_model",
    "TakeHeldRequests": "doeff_cluster._sim.local",
    "TrackedSpawn": "doeff_cluster._sim.local",
    "WaitsOf": "doeff_cluster._sim.local",
    "WallBackOf": "doeff_cluster._sim.local",
    "WarmRuntimeEnv": "doeff_cluster.shared.intent.warm_model",
    "WatchFailuresOf": "doeff_cluster._sim.local",
    "WorkerDied": "doeff_cluster._sim.local",
    "WorkerEnded": "doeff_cluster._sim.local",
    "WorkerOf": "doeff_cluster._sim.local",
    "WorkerWakes": "doeff_cluster.worker.intent.worker_model",
    "WorkersStopping": "doeff_cluster._sim.local",
    "WriteRoomFile": "doeff_cluster._sim.local",
    "WriteShared": "doeff_cluster.shared.intent.shared_model",
    "answer_of": "doeff_cluster._sim.k8s_supplied",
    "business_wait_tap": "doeff_cluster._sim.local",
    "coordinator_answers": "doeff_cluster._sim.local",
    "declared_job_specs": "doeff_cluster.worker.protocol.declared",
    "declared_reply_of_json": "doeff_cluster.worker.protocol.declared",
    "env_translation": "doeff_cluster.worker.protocol.env_translation",
    "environ_table_reader": "doeff_cluster._foundation.host_contract",
    "fence": "doeff_cluster._sim.local",
    "git_command": "doeff_cluster._sim.checkout_git_script",
    "heartbeat": "doeff_cluster._sim.local",
    "held_kube_services": "doeff_cluster._sim.k8s_services",
    "held_value_of": "doeff_cluster._sim.k8s_supplied",
    "held_whole_of": "doeff_cluster._sim.k8s_supplied",
    "host_answers": "doeff_cluster._sim.local",
    "http_request": "doeff_cluster.shared.protocol.inbox",
    "k8s_supplied_peer": "doeff_cluster._sim.k8s_supplied",
    "load_state": "doeff_cluster._coordinator.entry.main",
    "local_machine_cluster": "doeff_cluster._sim.machine",
    "machine_answers": "doeff_cluster._sim.machine",
    "no_business_timers": "doeff_cluster._sim.local",
    "now_epoch_ms": "doeff_cluster.shared.core.clock",
    "observe_requests": "doeff_cluster._sim.local",
    "process_notices": "doeff_cluster._sim.local",
    "process_signals": "doeff_cluster._sim.local",
    "process_versions": "doeff_cluster._foundation.process_versions",
    "promise_or_timeout": "doeff_cluster.shared.core.promise_wait",
    "read_grant_of": "doeff_cluster._sim.k8s_supplied",
    "readiness_awaited": "doeff_cluster.shared.protocol.coordinator_reads",
    "run_coordinator": "doeff_cluster._coordinator.core.program",
    "runtime_env_of_context": "doeff_cluster.shared.core.run_context_rules",
    "scenario_wait_tap": "doeff_cluster._sim.local",
    "service_account_token_file": "doeff_cluster._sim.k8s_supplied",
    "sha_of": "doeff_cluster._sim.worktree_git_script",
    "sim_cluster": "doeff_cluster._sim.local",
    "sim_host": "doeff_cluster._sim.local",
    "sim_world": "doeff_cluster._sim.local",
    "stand_in_store": "doeff_cluster._sim.test_recording_on_sim",
    "stopped": "doeff_cluster._sim.machine",
    "tick_pauses": "doeff_cluster.worker.protocol.tick_pauses",
    "wall_sim_cluster": "doeff_cluster._sim.local",
    "worker_wall": "doeff_cluster._sim.local",
    "worktree_git_answer": "doeff_cluster._sim.worktree_git_script",
    "worktree_git_command": "doeff_cluster._sim.worktree_git_script",
    "world_settings_reader": "doeff_cluster._sim.env_world",
    "AppendRecordLines": "doeff_cluster._record_store.intent.record_store_model",
    "CompactRecords": "doeff_cluster._record_store.intent.record_store_model",
    "ListRecordRuns": "doeff_cluster._record_store.intent.record_store_model",
    "PruneRecords": "doeff_cluster._record_store.intent.record_store_model",
    "ReadRecordRun": "doeff_cluster._record_store.intent.record_store_model",
    "http_requests": "doeff_cluster.shared.protocol.inbox",
    "record_files": "doeff_cluster._record_store.protocol.record_files",
    "stop_flag": "doeff_cluster.shared.protocol.inbox",
    "CLUSTER_API": "doeff_cluster._foundation.in_cluster_api",
    "ClusterCaMissing": "doeff_cluster._foundation.in_cluster_api",
    "DECISION": "doeff_cluster._foundation.record_codec",
    "EffectCodec": "doeff_cluster._foundation.record_codec",
    "FoundationClosure": "doeff_cluster._foundation.foundation_check",
    "HOST_CONTRACT": "doeff_cluster._foundation.host_contract",
    "MalformedRecordSpec": "doeff_cluster._foundation.record_codec",
    "READ": "doeff_cluster._foundation.record_codec",
    "RecordedError": "doeff_cluster._foundation.record_codec",
    "RecordingInstaller": "doeff_cluster._foundation.record_handlers",
    "ReplayFinished": "doeff_cluster._foundation.record_log",
    "SERVICE_ACCOUNT_CA_PATH": "doeff_cluster._foundation.in_cluster_api",
    "SIM_PASSABLE": "doeff_cluster._foundation.host_contract",
    "SourceUnidentified": "doeff_cluster._foundation.process_versions",
    "UnencodableValue": "doeff_cluster._foundation.record_codec",
    "UnreadDeclaration": "doeff_cluster._foundation.foundation_check",
    "UnrecordableEffect": "doeff_cluster._foundation.record_codec",
    "WalStore": "doeff_cluster._foundation.wal_store",
    "effect_recorder": "doeff_cluster._foundation.record_handlers",
    "effect_replayer": "doeff_cluster._foundation.record_handlers",
    "foundation_closure": "doeff_cluster._foundation.foundation_check",
    "hyx_closedXquestion_markX": "doeff_cluster._foundation.foundation_check",
    "in_cluster_api_connected": "doeff_cluster._foundation.in_cluster_api",
    "in_cluster_api_routed": "doeff_cluster._foundation.in_cluster_api",
    "kube_headers": "doeff_cluster._foundation.kube_client",
    "os_environ_reader": "doeff_cluster._foundation.host_contract",
    "register": "doeff_cluster._foundation.record_codec",
    "registered_types": "doeff_cluster._foundation.record_codec",
    "type_name": "doeff_cluster._foundation.record_codec",
    "ALIVE_MARK_MS": "doeff_cluster._coordinator.core.api_policy",
    "AnnotateDeployment": "doeff_cluster._coordinator.intent.kube_model",
    "BoardRow": "doeff_cluster._coordinator.intent.cluster_model",
    "BodyInvalid": "doeff_cluster.shared.intent.protocol",
    "ClusterJob": "doeff_cluster._coordinator.intent.cluster_model",
    "ClusterNaming": "doeff_cluster._coordinator.intent.cluster_model",
    "ClusterState": "doeff_cluster._coordinator.intent.cluster_model",
    "CoordinatorFault": "doeff_cluster._coordinator.intent.cluster_model",
    "CoordinatorUnsettled": "doeff_cluster._coordinator.intent.due_model",
    "ExtraProvides": "doeff_cluster._coordinator.intent.failover_model",
    "FailoverWorker": "doeff_cluster._coordinator.intent.failover_model",
    "FollowDeployments": "doeff_cluster._coordinator.intent.kube_model",
    "FollowNodes": "doeff_cluster._coordinator.intent.kube_model",
    "IMAGE_FOLLOW_KEYS": "doeff_cluster._coordinator.core.cluster_policy",
    "KeptApart": "doeff_cluster._coordinator.intent.failover_model",
    "KubeUnavailable": "doeff_cluster._coordinator.intent.kube_model",
    "ReadBody": "doeff_cluster._coordinator.intent.request_bodies",
    "Refused": "doeff_cluster._coordinator.core.resource_policy",
    "ResidentJob": "doeff_cluster._coordinator.intent.failover_model",
    "RuntimeEnvInvalid": "doeff_cluster.shared.intent.runtime_env_model",
    "SaveState": "doeff_cluster._coordinator.intent.cluster_model",
    "ScaleDeployment": "doeff_cluster._coordinator.intent.kube_model",
    "ServiceReached": "doeff_cluster.shared.intent.protocol",
    "UnplacedKind": "doeff_cluster._coordinator.intent.cluster_model",
    "WORKER_NOTICE_READS": "doeff_cluster._coordinator.protocol.worker_notices",
    "WORKER_NOTICE_ROUTES": "doeff_cluster._coordinator.protocol.worker_notices",
    "WalCorrupted": "doeff_cluster._coordinator.protocol.wal_format",
    "WorkerBack": "doeff_cluster._coordinator.intent.worker_notices",
    "WorkerGone": "doeff_cluster._coordinator.intent.worker_notices",
    "WorkerInfo": "doeff_cluster._coordinator.intent.cluster_model",
    "WorkerLoad": "doeff_cluster._coordinator.intent.cluster_model",
    "coordinator_faults": "doeff_cluster._coordinator.protocol.faults",
    "durable_states": "doeff_cluster._coordinator.protocol.store",
    "env_key": "doeff_cluster.shared.core.runtime_env_rules",
    "failover_refusals": "doeff_cluster._coordinator.core.failover_policy",
    "hyx_per_machineXquestion_markX": "doeff_cluster._coordinator.core.failover_policy",
    "kube_api": "doeff_cluster._coordinator.protocol.kube",
    "kube_memory": "doeff_cluster._coordinator.protocol.kube",
    "kube_unavailable": "doeff_cluster._coordinator.protocol.kube",
    "liveness_due": "doeff_cluster._coordinator.core.cluster_policy",
    "memory_misfit": "doeff_cluster._coordinator.core.cluster_policy",
    "memory_misfits": "doeff_cluster._coordinator.core.cluster_policy",
    "memory_room_of": "doeff_cluster._coordinator.core.cluster_policy",
    "memory_store": "doeff_cluster._coordinator.protocol.store",
    "naming_from_json": "doeff_cluster._coordinator.protocol.cluster_json",
    "next_wake": "doeff_cluster._coordinator.core.wake_policy",
    "placeable": "doeff_cluster._coordinator.core.cluster_policy",
    "queued_requests": "doeff_cluster._coordinator.protocol.request_queue",
    "reply_bodies": "doeff_cluster._coordinator.protocol.replies",
    "request_bodies": "doeff_cluster._coordinator.protocol.request_bodies",
    "responded": "doeff_cluster._coordinator.protocol.request_bodies",
    "room_file_workers": "doeff_cluster._coordinator.core.failover_room",
    "runtime_env_of_json": "doeff_cluster.shared.core.runtime_env_rules",
    "service_readiness": "doeff_cluster._coordinator.core.resource_policy",
    "spec_of_declaration": "doeff_cluster._coordinator.core.cluster_policy",
    "sweep_due": "doeff_cluster._coordinator.core.cluster_policy",
    "task_due": "doeff_cluster._coordinator.core.cluster_policy",
    "tick": "doeff_cluster._coordinator.core.api_policy",
    "tick_due": "doeff_cluster._coordinator.core.api_policy",
    "unplaced_kind": "doeff_cluster._coordinator.core.cluster_policy",
    "unplaced_memory": "doeff_cluster._coordinator.core.cluster_policy",
    "wal_store": "doeff_cluster._coordinator.protocol.store",
    "warm_key": "doeff_cluster.shared.core.warm_rules",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
