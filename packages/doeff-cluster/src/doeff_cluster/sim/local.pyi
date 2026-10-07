# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = local.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from collections.abc import Callable as Callable
from copy import deepcopy as deepcopy
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from pathlib import Path as Path
from urllib.parse import quote as url_quote
from doeff import with_handlers as with_handlers
from doeff import EffectBase as EffectBase
from doeff import UnhandledEffect as UnhandledEffect
from doeff import DoExpr as DoExpr
from doeff import Program as Program
from doeff_core_effects.effects import Ask as Ask
from doeff_core_effects.handlers import state as session_store
from doeff_core_effects.handlers import await_handler as await_handler
from doeff_core_effects.handlers import slog_handler as slog_handler
from doeff_core_effects.handlers import slog_discard_handler as slog_discard_handler
from doeff_core_effects.scheduler import scheduled as scheduled
from doeff_core_effects.scheduler import CreatePromise as CreatePromise
from doeff_core_effects.scheduler import CompletePromise as CompletePromise
from doeff_core_effects.scheduler import Wait as Wait
from doeff_core_effects.scheduler import Spawn as Spawn
from doeff_core_effects.scheduler import Gather as Gather
from doeff_core_effects.scheduler import Cancel as Cancel
from doeff_core_effects.scheduler import Discard as Discard
from doeff_core_effects.scheduler import Promise as Promise
from doeff_core_effects.scheduler import Task as Task
from doeff_core_effects.scheduler import Future as Future
from doeff_core_effects.scheduler import TaskCancelledError as TaskCancelledError
from doeff_core_effects.scheduler import Race as Race
from doeff_core_effects.stop_signal_effects import AwaitStop as AwaitStop
from doeff_core_effects.stop_signal_effects import StopRequested as StopRequested
from doeff_events import ArmedTimer as ArmedTimer
from doeff_events import ArmedTimers as ArmedTimers
from doeff_events import ArmedTimersEffect as ArmedTimersEffect
from doeff_events import WaitForEventEffect as WaitForEventEffect
from doeff_events import WaitForEventsEffect as WaitForEventsEffect
from doeff_time import Delay as Delay
from doeff_time import GetMonotonic as GetMonotonic
from doeff_time import epoch_ms_of as epoch_ms_of
from doeff_time import sim_time_handler as sim_time_handler
from doeff_time import async_time_handler as async_time_handler
from doeff_cluster.shared.core.clock import now_epoch_ms as now_epoch_ms
from doeff_cluster.shared.core.clock import datetime_of_epoch_ms as datetime_of_epoch_ms
from doeff_cluster.shared.core.timing_rules import scaled_timing as scaled_timing
from doeff_cluster.shared.intent.protocol import ClusterTiming as ClusterTiming
from doeff_cluster.shared.intent.protocol import Request as Request
from doeff_cluster.shared.intent.protocol import Reply as Reply
from doeff_cluster.shared.intent.protocol import CoordinatorStopRequested as CoordinatorStopRequested
from doeff_cluster.shared.intent.protocol import PlainText as PlainText
from doeff_cluster.shared.intent.due_model import DueAt as DueAt
from doeff_cluster.shared.intent.due_model import DueNow as DueNow
from doeff_cluster.shared.intent.due_model import DueNever as DueNever
from doeff_cluster.coordinator.intent.cluster_model import ClusterState as ClusterState
from doeff_cluster.coordinator.intent.cluster_model import ClusterNaming as ClusterNaming
from doeff_cluster.coordinator.intent.cluster_model import ENDED_PHASES as ENDED_PHASES
from doeff_cluster.shared.intent.protocol import NextRequests as NextRequests
from doeff_cluster.shared.intent.process_model import AwaitProcessEnded as AwaitProcessEnded
from doeff_cluster.shared.intent.process_model import ProcessEnded as ProcessEnded
from doeff_cluster.shared.intent.process_model import ProcessWaitExpired as ProcessWaitExpired
from doeff_cluster.coordinator.core.cluster_policy import fresh_task_prefix as fresh_task_prefix
from doeff_cluster.coordinator.core.program import run_coordinator as run_coordinator
from doeff_cluster.coordinator.entry.main import load_state as load_state
from doeff_cluster.coordinator.entry.main import with_running_commit as with_running_commit
from doeff_core_effects.process_effects import EnvEntry as EnvEntry
from doeff_core_effects.scripted_process import ProcessScript as ProcessScript
from doeff_core_effects.scripted_process import scripted_process_handler as scripted_process_handler
from doeff_cluster.foundation.coordinator_http import RESEND_PAUSE_SECONDS as RESEND_PAUSE_SECONDS
from doeff_cluster.shared.core.resend import IDEMPOTENT_DEADLINE_SECONDS as IDEMPOTENT_DEADLINE_SECONDS
from doeff_cluster.foundation.coordinator_inbox import StopState as StopState
from doeff_cluster.shared.protocol.inbox import http_request as http_request
from doeff_cluster.coordinator.entry.handler_sets import MemoryWalStore as MemoryWalStore
from doeff_cluster.coordinator.entry.handler_sets import emulated_handlers as emulated_handlers
from doeff_events import MemoryBroker as MemoryBroker
from doeff_events import EventBus as EventBus
from doeff_events import WaitForEvent as WaitForEvent
from doeff_events import subscribed_event_handler as subscribed_event_handler
from doeff_events import memory_notice_handler as memory_notice_handler
from doeff_events import notice_events_handler as notice_events_handler
from doeff_cluster.coordinator.intent.worker_notices import WorkerGone as WorkerGone
from doeff_cluster.coordinator.protocol.worker_notices import WORKER_NOTICE_READS as WORKER_NOTICE_READS
from doeff_cluster.coordinator.protocol.store import Persist as Persist
from doeff_cluster.coordinator.protocol.request_queue import RequestQueue as RequestQueue
from doeff_cluster.coordinator.protocol.request_queue import enqueue_request as enqueue_request
from doeff_cluster.coordinator.protocol.request_queue import nudge_takers as nudge_takers
from doeff_cluster.coordinator.protocol.request_queue import await_answer as await_answer
from doeff_cluster.shared.core.promise_wait import promise_or_timeout as promise_or_timeout
from doeff_cluster.coordinator.protocol.kube import KubeMemory as KubeMemory
from doeff_cluster.shared.protocol.declaration_requests import ServiceRead as ServiceRead
from doeff_cluster.shared.protocol.declaration_requests import CONFLICT_REREADS as CONFLICT_REREADS
from doeff_cluster.shared.protocol.declaration_requests import service_read as service_read
from doeff_cluster.shared.protocol.declaration_requests import reread_after_conflict as reread_after_conflict
from doeff_cluster.shared.protocol.declaration_requests import needed_programs as needed_programs
from doeff_cluster.shared.protocol.declaration_requests import body_of as service_body_of
from doeff_cluster.shared.protocol.detached import detached_path as detached_path
from doeff_cluster.shared.protocol.detached import detached_submit_body as detached_submit_body
from doeff_cluster.shared.protocol.detached import detached_refusal as detached_refusal
from doeff_cluster.shared.protocol.detached import submit_unreachable as submit_unreachable
from doeff_cluster.shared.protocol.detached import awaited_answer as awaited_answer
from doeff_cluster.shared.protocol.detached import runner_facts_of_view as runner_facts_of_view
from doeff_cluster.shared.protocol.detached import runners_unreachable as runners_unreachable
from doeff_cluster.shared.protocol.detached import warm_request_body as warm_request_body
from doeff_cluster.shared.protocol.detached import warm_path as warm_path
from doeff_cluster.shared.protocol.detached import absent_warm_state as absent_warm_state
from doeff_cluster.shared.protocol.detached import SERVER_ERROR as SERVER_ERROR
from doeff_cluster.shared.protocol.detached import warm_unconnected as warm_unconnected
from doeff_cluster.shared.protocol.detached import warm_server_failure as warm_server_failure
from doeff_cluster.shared.protocol.detached import runners_change_of as runners_change_of
from doeff_cluster.shared.protocol.detached import watch_query as watch_query
from doeff_cluster.shared.protocol.detached import service_ready_of as service_ready_of
from doeff_cluster.shared.protocol.detached import service_facts_of_view as service_facts_of_view
from doeff_cluster.shared.protocol.detached import services_unreachable as services_unreachable
from doeff_cluster.shared.core.capabilities import env_mapping as env_mapping
from doeff_cluster.shared.intent.detached_model import SubmitDetached as SubmitDetached
from doeff_cluster.shared.intent.detached_model import AwaitDetached as AwaitDetached
from doeff_cluster.shared.intent.detached_model import CancelDetached as CancelDetached
from doeff_cluster.shared.intent.detached_model import ReleaseDetached as ReleaseDetached
from doeff_cluster.shared.intent.detached_model import ReadRunners as ReadRunners
from doeff_cluster.shared.intent.detached_model import DetachedSubmitted as DetachedSubmitted
from doeff_cluster.shared.intent.detached_model import DetachedSubmitAnswer as DetachedSubmitAnswer
from doeff_cluster.shared.intent.detached_model import DetachedAwaited as DetachedAwaited
from doeff_cluster.shared.intent.detached_model import RunnersUnreachable as RunnersUnreachable
from doeff_cluster.shared.intent.detached_model import WARMING_PHASE as WARMING_PHASE
from doeff_cluster.shared.intent.detached_model import AwaitRunnersChange as AwaitRunnersChange
from doeff_cluster.shared.intent.detached_model import RunnersChangeAnswer as RunnersChangeAnswer
from doeff_cluster.shared.intent.detached_model import AwaitServiceReady as AwaitServiceReady
from doeff_cluster.shared.intent.detached_model import ServiceReady as ServiceReady
from doeff_cluster.shared.intent.detached_model import RunnersChange as RunnersChange
from doeff_cluster.shared.intent.detached_model import RunnersWatchMissing as RunnersWatchMissing
from doeff_cluster.shared.intent.detached_model import ReadServices as ReadServices
from doeff_cluster.shared.intent.detached_model import ServicesUnreachable as ServicesUnreachable
from doeff_cluster.worker.core.drain_client import DRAIN_DEADLINE_SECONDS as DRAIN_DEADLINE_SECONDS
from doeff_cluster.worker.core.drain_client import DRAIN_TTL_MARGIN_SECONDS as DRAIN_TTL_MARGIN_SECONDS
from doeff_cluster.worker.protocol.drain_requests import drain_request as drain_request
from doeff_cluster.worker.protocol.declared import DeclaredReply as DeclaredReply
from doeff_cluster.worker.protocol.declared import declared_reply_of_json as declared_reply_of_json
from doeff_cluster.worker.protocol.declared import declared_job_specs as declared_job_specs
from doeff_cluster.worker.protocol.declared import task_specs as task_specs
from doeff_cluster.worker.protocol.heartbeat import heartbeat_body as heartbeat_body
from doeff_cluster.worker.protocol.heartbeat import status_report as status_report
from doeff_cluster.worker.protocol.heartbeat import env_report as env_report
from doeff_cluster.worker.protocol.heartbeat import env_heartbeat_part as env_heartbeat_part
from doeff_cluster.worker.core.heartbeat_rules import desired_when_unreachable as desired_when_unreachable
from doeff_cluster.worker.core.heartbeat_rules import warm_env_of_row as warm_env_of_row
from doeff_cluster.worker.core.beat_policy import WatchKind as WatchKind
from doeff_cluster.worker.core.beat_policy import WatchReading as WatchReading
from doeff_cluster.worker.core.beat_policy import beat_interval_ms as beat_interval_ms
from doeff_cluster.worker.core.beat_policy import heartbeat_due as heartbeat_due
from doeff_cluster.worker.core.beat_policy import watch_reading as watch_reading
from doeff_cluster.worker.core.beat_policy import reply_revision as reply_revision
from doeff_cluster.worker.core.beat_policy import WATCH_RETRY_SECONDS as WATCH_RETRY_SECONDS
from doeff_cluster.worker.core.beat_policy import WAKE_HOLD_SECONDS as WAKE_HOLD_SECONDS
from doeff_cluster.worker.protocol.coordinator_link import watch_params as watch_params
from doeff_cluster.worker.protocol.coordinator_link import with_bell as with_bell
from doeff_cluster.worker.protocol.coordinator_link import RESEND_AFTER_MS as RESEND_AFTER_MS
from doeff_cluster.worker.core.heartbeat_rules import keep_marks_held as keep_marks_held
from doeff_cluster.worker.core.heartbeat_rules import desired_after_silence as desired_after_silence
from doeff_cluster.worker.protocol.tick_pauses import tick_pauses as tick_pauses
from doeff_cluster.worker.core.worker_due import beat_due as beat_due
from doeff_cluster.worker.core.worker_due import fence_due as fence_due
from doeff_cluster.worker.core.worker_due import due_after as due_after
from doeff_cluster.shared.core.due_policy import earliest_due as earliest_wake
from doeff_cluster.foundation.host_contract import HOST_CONTRACT as HOST_CONTRACT
from doeff_cluster.foundation.host_contract import SIM_PASSABLE as SIM_PASSABLE
from doeff_cluster.foundation.host_contract import environ_table_reader as environ_table_reader
from doeff_cluster.shared.intent.run_context import RunContext as RunContext
from doeff_cluster.shared.core.run_context_rules import worker_context_environ as worker_context_environ
from doeff_cluster.shared.core.run_context_rules import process_context_environ as process_context_environ
from doeff_cluster.shared.core.run_context_rules import context_of_environ as context_of_environ
from doeff_cluster.shared.core.run_context_rules import runtime_env_of_context as runtime_env_of_context
from doeff_cluster.worker.entry.job_entry import decoded_program as decoded_program
from doeff_cluster.shared.intent.metrics_model import ReportMetrics as ReportMetrics
from doeff_cluster.shared.intent.readiness_model import ReportReady as ReportReady
from doeff_cluster.shared.protocol.remote import task_submit_body as task_submit_body
from doeff_cluster.shared.protocol.remote import outcome_of as outcome_of
from doeff_cluster.shared.protocol.remote import settled_value as settled_value
from doeff_cluster.shared.intent.remote_model import RemoteJob as RemoteJob
from doeff_cluster.shared.intent.remote_model import RemoteJobFailed as RemoteJobFailed
from doeff_cluster.shared.intent.remote_model import TaskSucceeded as TaskSucceeded
from doeff_cluster.shared.intent.remote_model import TaskFailed as TaskFailed
from doeff_cluster.shared.protocol.program_codec import encode_program as encode_program
from doeff_cluster.shared.protocol.program_codec import encode_outcome as encode_outcome
from doeff_cluster.shared.core.remote_rules import failed_from as failed_from
from doeff_cluster.shared.core.remote_rules import program_sha as program_sha
from doeff_cluster.foundation.process_versions import process_versions as process_versions
from doeff_cluster.shared.protocol.task_result import task_result_request as task_result_request
from doeff_cluster.shared.protocol.task_result import task_id_of_job as task_id_of_job
from doeff_cluster.shared.protocol.service_report import report_request as report_request
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv as RuntimeEnv
from doeff_cluster.shared.intent.runtime_env_model import EnvFailure as EnvFailure
from doeff_cluster.shared.core.runtime_env_rules import hyx_runtime_env_XgreaterHthan_signXjson as hyx_runtime_env_XgreaterHthan_signXjson
from doeff_cluster.shared.core.native_wheel import current_platform as current_platform
from doeff_cluster.shared.intent.semaphore_model import LeaseOp as LeaseOp
from doeff_cluster.shared.intent.semaphore_model import LeaseAnswer as LeaseAnswer
from doeff_cluster.shared.intent.semaphore_model import AwaitLeaseFree as AwaitLeaseFree
from doeff_cluster.shared.intent.semaphore_model import SEMAPHORE_PREFIX as SEMAPHORE_PREFIX
from doeff_hy.wire import parse as parse_wire
from doeff_cluster.shared.core.lease_rules import drop_holders as drop_holders
from doeff_cluster.shared.core.lease_rules import lease_holder as lease_holder
from doeff_cluster.shared.core.lease_rules import holder_tokens_prefix as holder_tokens_prefix
from doeff_cluster.shared.entry.service_build import system_declaration as system_declaration
from doeff_cluster.shared.intent.service_model import System as System
from doeff_cluster.shared.intent.service_model import Declaration as Declaration
from doeff_cluster.shared.intent.cluster_control import ServiceReadiness as ServiceReadiness
from doeff_cluster.shared.intent.cluster_control import Redeclare as Redeclare
from doeff_cluster.shared.intent.cluster_control import ReadinessOf as ReadinessOf
from doeff_cluster.shared.intent.cluster_control import Crash as Crash
from doeff_cluster.shared.intent.cluster_control import KillWorker as KillWorker
from doeff_cluster.shared.intent.cluster_control import StopWorker as StopWorker
from doeff_cluster.shared.intent.cluster_control import StopCoordinator as StopCoordinator
from doeff_cluster.shared.intent.cluster_control import CrashCoordinator as CrashCoordinator
from doeff_cluster.shared.intent.cluster_control import AwaitReadiness as AwaitReadiness
from doeff_cluster.shared.intent.cluster_control import ServiceFailed as ServiceFailed
from doeff_cluster.shared.intent.cluster_control import ReadinessWaitExpired as ReadinessWaitExpired
from doeff_cluster.shared.intent.cluster_control import AwaitJobProcess as AwaitJobProcess
from doeff_cluster.shared.intent.cluster_control import JobProcessSeen as JobProcessSeen
from doeff_cluster.shared.intent.cluster_control import JobProcessWaitExpired as JobProcessWaitExpired
from doeff_cluster.shared.protocol.coordinator_reads import readiness_of_body as readiness_of_body
from doeff_cluster.shared.protocol.coordinator_reads import readiness_wait_answer as readiness_wait_answer
from doeff_cluster.shared.protocol.board_requests import board_read_request as board_read_request
from doeff_cluster.shared.protocol.board_requests import board_write_request as board_write_request
from doeff_cluster.shared.protocol.board_requests import lease_request as lease_request
from doeff_cluster.shared.protocol.board_requests import lease_wait_request as lease_wait_request
from doeff_cluster.shared.protocol.board_requests import lease_wait_answer as lease_wait_answer
from doeff_cluster.shared.intent.shared_model import ReadShared as ReadShared
from doeff_cluster.shared.intent.shared_model import WriteShared as WriteShared
from doeff_cluster.shared.intent.shared_model import ANY as ANY
from doeff_cluster.shared.intent.warm_model import WarmRuntimeEnv as WarmRuntimeEnv
from doeff_cluster.shared.intent.warm_model import ReadWarmState as ReadWarmState
from doeff_cluster.shared.intent.warm_model import WarmState as WarmState
from doeff_cluster.shared.intent.warm_model import WarmAnswer as WarmAnswer
from doeff_cluster.shared.intent.warm_model import AwaitWarm as AwaitWarm
from doeff_cluster.shared.intent.warm_model import WarmReady as WarmReady
from doeff_cluster.shared.intent.warm_model import WarmFailed as WarmFailed
from doeff_cluster.shared.intent.warm_model import WarmWaitExpired as WarmWaitExpired
from doeff_cluster.shared.core.warm_rules import warm_state_of_json as warm_state_of_json
from doeff_cluster.shared.core.warm_rules import warm_wait_answer as warm_wait_answer
from doeff_cluster.worker.entry.main import worker_on as worker_on
from doeff_cluster.worker.intent.worker_model import WorkerPolicy as WorkerPolicy
from doeff_cluster.worker.intent.worker_model import WorkerState as WorkerState
from doeff_cluster.worker.intent.worker_model import WorldView as WorldView
from doeff_cluster.worker.intent.worker_model import CodeView as CodeView
from doeff_cluster.worker.intent.worker_model import CodeState as CodeState
from doeff_cluster.worker.intent.worker_model import ProcessView as ProcessView
from doeff_cluster.worker.intent.worker_model import ProbeView as ProbeView
from doeff_cluster.worker.intent.worker_model import ProbeState as ProbeState
from doeff_cluster.worker.intent.worker_model import DesiredJobs as DesiredJobs
from doeff_cluster.worker.intent.worker_model import DesiredUnreadable as DesiredUnreadable
from doeff_cluster.worker.intent.worker_model import ReadDesired as ReadDesired
from doeff_cluster.worker.intent.worker_model import ObserveWorld as ObserveWorld
from doeff_cluster.worker.intent.worker_model import PublishStatus as PublishStatus
from doeff_cluster.worker.intent.worker_model import PrepareCode as PrepareCode
from doeff_cluster.worker.intent.worker_model import PrepareEnv as PrepareEnv
from doeff_cluster.worker.intent.worker_model import SweepEnvs as SweepEnvs
from doeff_cluster.worker.intent.worker_model import StartJob as StartJob
from doeff_cluster.worker.intent.worker_model import SignalJob as SignalJob
from doeff_cluster.worker.intent.worker_model import ReapJob as ReapJob
from doeff_cluster.worker.intent.worker_model import RetireJob as RetireJob
from doeff_cluster.worker.intent.worker_model import ProbeEntry as ProbeEntry
from doeff_cluster.worker.intent.worker_model import ForgetProbes as ForgetProbes
from doeff_cluster.worker.intent.worker_model import ReleaseLeases as ReleaseLeases
from doeff_cluster.worker.intent.worker_model import EnvReport as EnvReport
from doeff_cluster.worker.intent.worker_model import AwaitNextTick as AwaitNextTick
from doeff_cluster.worker.intent.worker_model import WakeSet as WakeSet
from doeff_cluster.worker.intent.worker_model import WorkerWakes as WorkerWakes
from doeff_cluster.worker.intent.worker_model import StopStage as StopStage
from doeff_cluster.worker.intent.worker_model import StopProgress as StopProgress
from doeff_cluster.worker.intent.worker_model import WarmChildMark as WarmChildMark
from doeff_cluster.worker.intent.worker_model import WarmChildView as WarmChildView
from doeff_cluster.worker.intent.worker_model import StartWarmChild as StartWarmChild
from doeff_cluster.worker.intent.worker_model import StopWarmChild as StopWarmChild
from doeff_cluster.worker.intent.worker_model import ForgetWarmChild as ForgetWarmChild
from doeff_cluster.worker.intent.worker_model import NoticeJob as NoticeJob
from doeff_cluster.worker.intent.worker_model import Retired as Retired
from doeff_cluster.worker.intent.worker_model import HandoffAbandoned as HandoffAbandoned
from doeff_cluster.shared.intent.job_model import JobSpec as JobSpec
from doeff_cluster.shared.core.job_rules import spec_hash as spec_hash
from doeff_cluster.worker.intent.retirement_model import AwaitRetirement as AwaitRetirement
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put
NO_STATE_FILE: str
SIM_URL: str
SIM_START_MS: int
DECLARE_ACTOR: str
CLIENT_NAME: str
TASK_POLL_SECONDS: float
TASK_LEASE_SECONDS: float
DETACHED_POLL_SECONDS: float
QUEUE_WAIT_SECONDS: float
WORKER_WAIT_SECONDS: float
DRAIN_TTL_SECONDS: float
REPORT_KINDS: tuple[str, ...]
TERM_REASON: str
KILLED_CODE: int
PAUSE_STOP: str
PAUSE_CRASH: str
CUT_REASON: str
FAULT_REASON: str
SIM_TIMING_RATIO: int

@dataclass(frozen=True, kw_only=True)
class SimWorker:
    name: str
    provides: frozenset
    task_reserve: int
    doeff_commit: str = ''
    exclusive: frozenset = ...
    capacity: int = 10
    node: str = ''
    versions: dict | None = None
    prepare_seconds: float = 0.0
    env_prepare_seconds: float | None = None
    env_failure: EnvFailure | None = None
    starts_down: bool = False
    ignores_fence: bool = False
    beat_every_ms: int | None = None
    retire_stops: bool = False
    ignores_keep_marks: bool = False
    silent_stop: bool = False
    overstates_capacity: int | None = None
    claims_provides: frozenset | None = None
    claims_exclusive: frozenset | None = None
    fresh_boot_every_beat: bool = False
    hides_retired: bool = False
    claims_task_reserve: int | None = None
    silent_notices: bool = False

@dataclass(frozen=True, kw_only=True)
class SimProcess:
    job: str
    worker: str
    instance: str
    attempt: int
    pid: int
    spec_hash: str
    started_ms: int
    ended_ms: int | None = None
    exit_code: int | None = None
    detail: str = ''
    value: object = None

@dataclass(frozen=True, kw_only=True)
class SimReport:
    job: str
    kind: str
    instance: str
    at: int
    ready: bool | None
    reason: str
    role: str
    metrics: dict | None

@dataclass(frozen=True, kw_only=True)
class SimPreparation:
    worker: str
    key: str
    env: bool
    warm: bool
    started_ms: int
    ready_ms: int
    failure: EnvFailure | None

@dataclass(frozen=True, kw_only=True)
class SimWatchFailure:
    worker: str
    boot: str
    at: int
    reason: str

@dataclass(frozen=True, kw_only=True)
class SimCoordinatorRun:
    started_ms: int
    ended_ms: int | None = None
    outcome: str = ''

@dataclass(frozen=True, kw_only=True)
class RouteFaults:
    cut: frozenset
    failing: dict
    now_ms: int

@dataclass(frozen=True, kw_only=True)
class Admission:
    faults: RouteFaults
    kept: tuple

@dataclass(frozen=True, kw_only=True)
class CoordinatorStep:
    at: int
    writes: tuple

@dataclass(frozen=True, kw_only=True)
class SimPauses:
    queued: tuple
    downtime: float | None = None
    restart_ms: int | None = None

@dataclass(frozen=True, kw_only=True)
class SimIntake:
    cuts: dict
    failing: dict
    held: tuple
    reports: tuple

@dataclass(frozen=True, kw_only=True)
class SimLink:
    queue: RequestQueue
    actor: str
    revision: str
    peer: str
    versions: dict
    timing: ClusterTiming
    runtime_env: RuntimeEnv | None = None

@_doeff_dataclass(frozen=True)
class DeclareRollout(_doeff_effect_base[dict]):
    name: str
    spec: dict

@_doeff_dataclass(frozen=True)
class KubeCalls(_doeff_effect_base[tuple]):
    ...

@_doeff_dataclass(frozen=True)
class KubeReads(_doeff_effect_base[tuple]):
    ...

@_doeff_dataclass(frozen=True)
class SettleDeployment(_doeff_effect_base[None]):
    namespace: str
    name: str
    ready: int | None = None

@_doeff_dataclass(frozen=True)
class NodeReads(_doeff_effect_base[tuple]):
    ...

@_doeff_dataclass(frozen=True)
class RelabelNode(_doeff_effect_base[None]):
    name: str
    labels: dict

@_doeff_dataclass(frozen=True)
class ReportsOf(_doeff_effect_base[tuple]):
    name: str

@_doeff_dataclass(frozen=True)
class ProcessesOf(_doeff_effect_base[tuple]):
    name: str

@_doeff_dataclass(frozen=True)
class AwaitProcessStarted(_doeff_effect_base[SimProcess]):
    name: str

@_doeff_dataclass(frozen=True)
class SharedRows(_doeff_effect_base[dict]):
    prefix: str

@_doeff_dataclass(frozen=True)
class ReadCoordinator(_doeff_effect_base[dict | PlainText]):
    path: str

@_doeff_dataclass(frozen=True)
class FailRoute(_doeff_effect_base[None]):
    method: str
    path: str
    status: int
    seconds: float

@_doeff_dataclass(frozen=True)
class ReplaceCoordinatorEnviron(_doeff_effect_base[None]):
    environ: tuple[EnvEntry, ...]

@_doeff_dataclass(frozen=True)
class CoordinatorEnvironOf(_doeff_effect_base[tuple[EnvEntry, ...]]):
    ...

@_doeff_dataclass(frozen=True)
class CoordinatorRuns(_doeff_effect_base[tuple]):
    ...

@_doeff_dataclass(frozen=True)
class StartWorker(_doeff_effect_base[bool]):
    name: str

@_doeff_dataclass(frozen=True)
class ReplaceWorker(_doeff_effect_base[bool]):
    name: str
    worker: SimWorker

@_doeff_dataclass(frozen=True)
class WorkerOf(_doeff_effect_base[SimWorker]):
    name: str

@_doeff_dataclass(frozen=True)
class CutWorker(_doeff_effect_base[None]):
    name: str
    seconds: float

@_doeff_dataclass(frozen=True)
class StallWorker(_doeff_effect_base[None]):
    name: str
    seconds: float

@_doeff_dataclass(frozen=True)
class DrainWorker(_doeff_effect_base[dict]):
    name: str
    ttl_seconds: float = ...

@_doeff_dataclass(frozen=True)
class PreparationsOf(_doeff_effect_base[tuple]):
    name: str

@_doeff_dataclass(frozen=True)
class WatchFailuresOf(_doeff_effect_base[tuple]):
    name: str

@_doeff_dataclass(frozen=True)
class ClientLink(_doeff_effect_base[SimLink]):
    ...

@dataclass(frozen=True, kw_only=True)
class SimOutside:
    handlers: list
    effects: tuple
    per_process: Callable | None = None

@dataclass(frozen=True, kw_only=True)
class SimPlan:
    system: System
    declaration: Declaration
    workers: tuple
    environ: dict
    revision: str
    versions: dict
    start_ms: int
    timing: ClusterTiming
    naming: ClusterNaming
    policy: WorkerPolicy
    passable: tuple
    notice_broker: MemoryBroker
    per_process: Callable | None = None
    store: Callable | None = None
    deployments: dict | None = None
    nodes: dict | None = None
    runtime_env: RuntimeEnv | None = None
    watches_gone: bool = False

@dataclass(frozen=True, kw_only=True)
class SimParts:
    queue: RequestQueue
    store: MemoryWalStore
    stop: StopState
    kube: KubeMemory
    broker: MemoryBroker

@dataclass(frozen=True, kw_only=True)
class SimExit:
    code: int
    result: str | None
    detail: str = ''
    value: object = None

@dataclass(frozen=True, kw_only=True)
class HostTruth:
    boot: str
    boot_at: int
    processes: tuple
    codes: dict
    probes: dict
    statuses: list
    last_ok_ms: int
    fence_ms: int
    last_desired: tuple
    last_warm: tuple
    programs: dict
    results: dict
    task_echo: dict
    beats: int = 0
    down: bool = False
    stopping: bool = False
    fresh: bool = False
    sent_statuses: list | None = None
    beat_interval_ms: int = ...
    watch_after: int | None = None
    watch_confirmed: bool = False
    watch_unsupported: bool = False
    woken: bool = False
    beat_bells: tuple = ...
    watch_failure: str | None = None
    tick_bell: Promise | None = None
    stalled_until_ms: int = 0
    keep_fence_ms: int = ...
    sent_stopping: bool = False
    wake_bell: Promise | None = None
    stop_bell: Promise | None = None
    ticks: int = 0
    warm_children: tuple = ...

@dataclass(frozen=True, kw_only=True)
class HostTruthChange:
    before: HostTruth
    after: HostTruth | None

@dataclass(frozen=True, kw_only=True)
class SimStopBox:
    reason: str | None = None
    waiters: tuple = ...
    bridged: bool = False

@dataclass(frozen=True, kw_only=True)
class SimStopWait:
    promise: Promise
    bridge: bool

@dataclass(frozen=True, kw_only=True)
class SimNoticeWaiter:
    after: Retired | HandoffAbandoned | None
    promise: Promise

@dataclass(frozen=True, kw_only=True)
class SimNoticeBox:
    notice: Retired | HandoffAbandoned | None = None
    waiters: tuple = ...

@dataclass(frozen=True, kw_only=True)
class HostStop:
    truth: HostTruth
    all_stopping: bool

class WorkerDied(Exception):
    ...

@_doeff_dataclass(frozen=True)
class PlanOf(_doeff_effect_base[SimPlan]):
    ...

@_doeff_dataclass(frozen=True)
class PartsOf(_doeff_effect_base[SimParts]):
    ...

@_doeff_dataclass(frozen=True)
class HostTruthOf(_doeff_effect_base[HostTruth]):
    name: str

@_doeff_dataclass(frozen=True)
class PutHostTruth(_doeff_effect_base[None]):
    name: str
    truth: HostTruth

@_doeff_dataclass(frozen=True)
class ChangeHostTruth(_doeff_effect_base[HostTruthChange]):
    name: str
    boot: str
    live: bool
    change: Callable

@_doeff_dataclass(frozen=True)
class NextPid(_doeff_effect_base[int]):
    ...

@_doeff_dataclass(frozen=True)
class KeepHandle(_doeff_effect_base[None]):
    pid: int
    task: Task

@_doeff_dataclass(frozen=True)
class HandleOf(_doeff_effect_base[Task | None]):
    pid: int

@_doeff_dataclass(frozen=True)
class KeepChild(_doeff_effect_base[None]):
    pid: int
    task: Task

@_doeff_dataclass(frozen=True)
class ProcessStopAsked(_doeff_effect_base[str | None]):
    pid: int

@_doeff_dataclass(frozen=True)
class ProcessStopWait(_doeff_effect_base[SimStopWait]):
    pid: int
    bridge: bool

@_doeff_dataclass(frozen=True)
class ProcessStopRaised(_doeff_effect_base[bool]):
    pid: int
    reason: str

@_doeff_dataclass(frozen=True)
class ProcessNoticeWait(_doeff_effect_base[Promise]):
    pid: int
    after: Retired | HandoffAbandoned | None

@_doeff_dataclass(frozen=True)
class ProcessNoticeRaised(_doeff_effect_base[None]):
    pid: int
    notice: Retired | HandoffAbandoned

@_doeff_dataclass(frozen=True)
class NoteProcess(_doeff_effect_base[None]):
    process: SimProcess

@_doeff_dataclass(frozen=True)
class EndProcess(_doeff_effect_base[None]):
    worker: str
    pid: int
    ended: SimExit

@_doeff_dataclass(frozen=True)
class NoteCoordinatorWrite(_doeff_effect_base[None]):
    writes: tuple

@_doeff_dataclass(frozen=True)
class NotePreparation(_doeff_effect_base[None]):
    preparation: SimPreparation

@_doeff_dataclass(frozen=True)
class NoteWatchFailure(_doeff_effect_base[None]):
    failure: SimWatchFailure

@_doeff_dataclass(frozen=True)
class WorkersStopping(_doeff_effect_base[bool]):
    ...

@_doeff_dataclass(frozen=True)
class StopWorkers(_doeff_effect_base[None]):
    ...

@_doeff_dataclass(frozen=True)
class WorkerEnded(_doeff_effect_base[None]):
    name: str
    boot: str

@_doeff_dataclass(frozen=True)
class RevivalOf(_doeff_effect_base[Promise | None]):
    name: str

@_doeff_dataclass(frozen=True)
class AdmitBatch(_doeff_effect_base[Admission]):
    batch: tuple

@_doeff_dataclass(frozen=True)
class CoordinatorSteps(_doeff_effect_base[tuple]):
    ...

@_doeff_dataclass(frozen=True)
class ReleaseRequest(_doeff_effect_base[None]):
    request: Request

@_doeff_dataclass(frozen=True)
class TakeHeldRequests(_doeff_effect_base[tuple]):
    ...

@_doeff_dataclass(frozen=True)
class PauseDue(_doeff_effect_base[bool]):
    kind: str

@_doeff_dataclass(frozen=True)
class StepEnded(_doeff_effect_base[bool]):
    released: tuple
    writes: tuple

@_doeff_dataclass(frozen=True)
class DowntimeOf(_doeff_effect_base[float | None]):
    ...

@_doeff_dataclass(frozen=True)
class PersistCrashDue(_doeff_effect_base[bool]):
    ...

@_doeff_dataclass(frozen=True)
class NextWorldDue(_doeff_effect_base[int | None]):
    now_ms: int

@_doeff_dataclass(frozen=True)
class StopRequestOf(_doeff_effect_base[HostStop]):
    name: str

@_doeff_dataclass(frozen=True)
class CoordinatorStarted(_doeff_effect_base[None]):
    ms: int

@_doeff_dataclass(frozen=True)
class CoordinatorEnded(_doeff_effect_base[None]):
    ms: int
    outcome: str

@dataclass(frozen=True, kw_only=True)
class BusinessWait:
    pid: int
    job: str
    events: str

@dataclass(frozen=True, kw_only=True)
class LiveProcess:
    pid: int
    job: str
    tasks: int

@dataclass(frozen=True, kw_only=True)
class WaitsSeen:
    live: tuple[LiveProcess, ...]
    waits: tuple[BusinessWait, ...]
    scenario_waiting: bool

@dataclass(frozen=True, kw_only=True)
class WaitSnapshot:
    live: tuple[LiveProcess, ...]
    waits: tuple[BusinessWait, ...]
    scenario_waiting: bool
    rows_settled: bool
    armed_timers: tuple[ArmedTimer, ...] = ...
    world_due: int | None = None

@dataclass(frozen=True, kw_only=True)
class SimDeadlock:
    waits: tuple[BusinessWait, ...]
    at_ms: int | None = None

class SimDeadlockError(RuntimeError):
    ...

class SimLivenessError(RuntimeError):
    ...

@_doeff_dataclass(frozen=True)
class NoteEventWait(_doeff_effect_base[None]):
    pid: int
    events: str
    waiting: bool

@_doeff_dataclass(frozen=True)
class NoteScenarioWait(_doeff_effect_base[None]):
    waiting: bool

@_doeff_dataclass(frozen=True)
class ArmWaitChange(_doeff_effect_base[Promise]):
    ...

@_doeff_dataclass(frozen=True)
class WaitsOf(_doeff_effect_base[WaitsSeen]):
    ...

def default_workers(system: System) -> _Program[tuple, object]:
    ...

def declaration_of(system: System, revision: str, environ: dict, runtime_env: RuntimeEnv | None, versions: dict) -> _Program[Declaration, object]:
    ...

def sim_plan(system: System, workers: tuple | None, environ: dict | None, revision: str, start_ms: int, timing: ClusterTiming | None, policy: WorkerPolicy | None, outside: SimOutside | None, store: Callable | None, deployments: dict | None=None, runtime_env: RuntimeEnv | None=None, nodes: dict | None=None, *, notice_broker: MemoryBroker) -> _Program[SimPlan, object]:
    ...

def parts_of(plan: SimPlan) -> _Program[SimParts, object]:
    ...

def fresh_truth(name: str, generation: int, now: int, timing: ClusterTiming) -> _Program[HostTruth, object]:
    ...

def fresh_hosts(plan: SimPlan) -> _Program[dict, object]:
    ...

def reports_in(batch: list, now: int) -> _Program[tuple, object]:
    ...

def code_view_of(preparation: SimPreparation, now: int) -> _Program[CodeView, object]:
    ...

def codes_view(codes: dict, now: int) -> _Program[tuple, object]:
    ...

def view_of(truth: HostTruth, now: int) -> _Program[WorldView, object]:
    ...

def run_context_of(worker: str, spec: JobSpec, attempt: int, instance: str) -> _Program[RunContext, object]:
    ...

def program_path_of(sha: str | None) -> _Program[str, object]:
    ...

def control_link(queue: RequestQueue, revision: str, versions: dict, timing: ClusterTiming) -> _Program[SimLink, object]:
    ...

def send_request(link: SimLink, method: str, path: str, query: dict, body: dict | None) -> _Program[tuple, object]:
    ...

def send_resent(link: SimLink, method: str, path: str, query: dict, body: dict | None) -> _Program[tuple, object]:
    ...

def send_shaped(link: SimLink, shape: tuple) -> _Program[tuple, object]:
    ...

def send_shaped_resent(link: SimLink, shape: tuple) -> _Program[tuple, object]:
    ...

def answered_body(answer: tuple, what: str) -> dict | list | str | int | float | bool | None | PlainText:
    ...

def refused_or_body(answer: tuple, what: str) -> dict | list | str | int | float | bool | None | PlainText:
    ...

def object_body(body: dict | list | str | int | float | bool | None | PlainText, what: str) -> dict:
    ...

def answered_object(answer: tuple, what: str) -> dict:
    ...

def refused_or_object(answer: tuple, what: str) -> dict:
    ...

def unreached_reason(answer: tuple) -> str:
    ...

def board_written(answer: tuple) -> bool:
    ...

def remote_outcome(link: SimLink, program: Program | EffectBase, needs: frozenset, name: str, environ: dict) -> _Program[TaskSucceeded | TaskFailed, object]:
    ...

def declared_env(env: RuntimeEnv | None) -> _Program[dict | None, object]:
    ...

def submit_detached(link: SimLink, program: Program | EffectBase, key: str, needs: frozenset, name: str, lease_seconds: float, retain_seconds: float, environ: dict) -> _Program[DetachedSubmitAnswer, object]:
    ...

def bell_span(view: dict | None, timeout_seconds: float | int | None, waited: float) -> _Program[float | None, object]:
    ...

def await_detached(link: SimLink, key: str, timeout_seconds: float | int | None) -> _Program[DetachedAwaited, object]:
    ...

def ended_task_keys(writes: tuple) -> _Program[frozenset, object]:
    ...

def ring_ended_tasks(queue: RequestQueue, writes: tuple) -> _Program[int, object]:
    ...

def read_runners(link: SimLink) -> _Program[tuple | RunnersUnreachable, object]:
    ...

def read_services(link: SimLink) -> _Program[tuple | ServicesUnreachable, object]:
    ...

def await_runners_change(link: SimLink, after: int, timeout_seconds: float) -> _Program[RunnersChangeAnswer, object]:
    ...

def await_service_ready(link: SimLink, name: str) -> _Program[ServiceReady, object]:
    ...

def warm_answer_of(answer: tuple, what: str) -> WarmAnswer:
    ...

def warm_write(link: SimLink, env: RuntimeEnv, needs: frozenset, ttl_seconds: float, holder: str) -> _Program[WarmAnswer, object]:
    ...

def warm_read(link: SimLink, key: str) -> _Program[WarmAnswer, object]:
    ...

def await_warm(link: SimLink, key: str, timeout_seconds: float) -> _Program[WarmReady | WarmFailed | WarmWaitExpired, object]:
    ...

class TrackedSpawn(Spawn):
    ...

def tracked_child(pid: int, body: Program | EffectBase, priority: int, daemon: bool) -> _Program[Incomplete, object]:
    ...

def fence(pid: int, passable: tuple) -> _Handler:
    ...

def stop_bridge(pid: int) -> _Program[None, object]:
    ...

def process_signals(pid: int, passable: tuple) -> _Handler:
    ...

def process_notices(pid: int) -> _Handler:
    ...

@dataclass(frozen=True, kw_only=True)
class SimChild:
    ctx: RunContext
    program_path: str
    environ: dict
    link: SimLink
    pid: int
    passable: tuple
    outside: tuple = ...

def send_report(child: SimChild, kind: str, payload: dict) -> _Program[None, object]:
    ...

def host_answers(child: SimChild) -> _Handler:
    ...

def coordinator_answers(link: SimLink) -> _Handler:
    ...

@dataclass(frozen=True, kw_only=True)
class ProcessOutside:
    handlers: tuple
    effects: tuple = ...

def process_outside(per_process: Callable | None, job: str, worker: str) -> _Program[ProcessOutside, object]:
    ...

def run_fenced(program: DoExpr, child: SimChild, once: bool) -> _Program[SimExit, object]:
    ...

def refused_exit(refusal: RemoteJobFailed, once: bool) -> _Program[SimExit, object]:
    ...

def deliver_task_result(child: SimChild, result: str) -> _Program[None, object]:
    ...

def sim_process(worker: str, spec: JobSpec, child: SimChild, blob: str | None) -> _Program[None, object]:
    ...

def live_truth(name: str, boot: str) -> _Program[HostTruth, object]:
    ...

def checked_truth(name: str, boot: str, truth: HostTruth) -> _Program[HostTruth, object]:
    ...

def change_live_truth(name: str, boot: str, change: Callable) -> _Program[HostTruthChange, object]:
    ...

def statuses_written(truth: HostTruth, statuses: tuple) -> _Program[HostTruth, object]:
    ...

def accepted_programs(link: SimLink, wanted: list, known: dict) -> _Program[dict, object]:
    ...

def beat_body(worker: SimWorker, truth: HostTruth, sent_at: int, stopping: bool, plan: SimPlan) -> _Program[dict, object]:
    ...

def heartbeat(worker: SimWorker, boot: str, stopping: bool, plan: SimPlan, parts: SimParts) -> _Program[DesiredJobs | DesiredUnreadable, object]:
    ...

def release_leases(link: SimLink, job: str, instance: str) -> _Program[int, object]:
    ...

def begin_preparation(worker: SimWorker, truth: HostTruth, key: str, env: bool, warm: bool, now: int) -> _Program[None, object]:
    ...

def prepare(worker: SimWorker, boot: str, key: str, env: bool, warm: bool) -> _Program[None, object]:
    ...

def rehung_bell(bell: Promise | None) -> _Program[Promise, object]:
    ...

def ticked_truth(truth: HostTruth) -> _Program[HostTruth, object]:
    ...

def host_wakes(worker: SimWorker, truth: HostTruth, now: int, bell: Future | None) -> _Program[WakeSet, object]:
    ...

def sim_host(worker: SimWorker, boot: str, plan: SimPlan, parts: SimParts) -> _Handler:
    ...

def await_beat(name: str, boot: str) -> _Program[None, object]:
    ...

def note_watch(name: str, boot: str, after: int, reading: WatchReading) -> _Program[bool, object]:
    ...

def watch_desired(worker: SimWorker, boot: str) -> _Program[str, object]:
    ...

def guarded_watch(worker: SimWorker, boot: str) -> _Program[str, object]:
    ...

def run_sim_worker(worker: SimWorker, policy: WorkerPolicy, boot: str) -> _Program[str, object]:
    ...

def generation_end(loop: Task) -> _Program[str, object]:
    ...

def worker_keeper(worker: SimWorker, policy: WorkerPolicy) -> _Program[str, object]:
    ...

@dataclass
class StepBook:
    released: tuple = ...
    noted: tuple = ...
    stopped: bool = False

def observe_requests(book: StepBook, queue: RequestQueue) -> _Handler:
    ...

def fail_open_requests(queue: RequestQueue, held: tuple, reason: str) -> _Program[int, object]:
    ...

def coordinator_life(plan: SimPlan, parts: SimParts) -> _Program[str, object]:
    ...

def coordinator_pod() -> _Program[int, object]:
    ...

def await_coordinator(queue: RequestQueue) -> _Program[None, object]:
    ...

def first_beats_missing(names: tuple) -> _Program[bool, object]:
    ...

def await_workers(names: tuple) -> _Program[None, object]:
    ...

def apply_declaration(link: SimLink, declaration: Declaration) -> _Program[tuple, object]:
    ...

def sim_service_current(link: SimLink, name: str, path: str) -> _Program[dict | None, object]:
    ...

def sim_service_written(link: SimLink, read: ServiceRead) -> _Program[tuple, object]:
    ...

def sim_service_written_rereading(link: SimLink, read: ServiceRead) -> _Program[tuple, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class NextWaiter:
    job: str
    excluding: tuple[int, ...]
    promise: Promise

def job_process_outside(log: tuple, job: str, excluding: tuple[int, ...]) -> _Program[SimProcess | None, object]:
    ...

def service_answer_of(link: SimLink, name: str) -> _Program[tuple, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class DueWaiters:
    remaining: dict
    due: tuple

def ended_process(log: tuple, job: str) -> _Program[SimProcess | None, object]:
    ...

def first_process(log: tuple, job: str) -> _Program[SimProcess | None, object]:
    ...

def ended_answer(process: SimProcess) -> _Program[ProcessEnded, object]:
    ...

def due_end_waiters(log: tuple, waiters: dict) -> _Program[DueWaiters, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class ProcessEnds:
    hosts: dict
    log: tuple
    handles: dict
    children: dict
    finished: frozenset
    end_waiters: dict
    stopped: tuple
    due: tuple
    bells: tuple

def end_process(ends: ProcessEnds, worker: str, pid: int, ended: SimExit, now: int) -> _Program[ProcessEnds, object]:
    ...

def end_processes(ends: ProcessEnds, victims: tuple, ended: SimExit, now: int) -> _Program[ProcessEnds, object]:
    ...

def settle_process_ends(ends: ProcessEnds, killed: bool) -> _Program[None, object]:
    ...

def pause_taken(pausing: SimPauses, kind: str) -> _Program[SimPauses | None, object]:
    ...

def next_world_due(intake: SimIntake, hosts: dict, pausing: SimPauses, now_ms: int) -> _Program[int | None, object]:
    ...

def sim_world(plan: SimPlan) -> _Handler:
    ...

def event_names(event_types: tuple) -> _Program[str, object]:
    ...

def without_one_wait(waits: tuple, pid: int, events: str) -> _Program[tuple, object]:
    ...

def live_processes(handles: dict, children: dict, log: tuple) -> _Program[tuple, object]:
    ...

def deadlock_of(snapshot: WaitSnapshot) -> _Program[SimDeadlock | None, object]:
    ...

def waits_closed(seen: WaitsSeen) -> _Program[bool, object]:
    ...

def task_rows_settled(link: SimLink) -> _Program[bool, object]:
    ...

def deadlock_text(found: SimDeadlock) -> _Program[str, object]:
    ...

def business_wait_tap(pid: int) -> _Handler:
    ...
scenario_wait_tap: _Handler

def earliest_due(world_due: int | None, armed: tuple) -> _Program[int | None, object]:
    ...
no_business_timers: _Handler

def deadlock_watch(link: SimLink) -> _Program[None, object]:
    ...
GONE_READER: str

def gone_watch(broker: MemoryBroker) -> _Program[None, object]:
    ...

def sim_main(scenario: Program | EffectBase) -> _Program[Incomplete, object]:
    ...

def sim_under_clock(system: System, scenario: Program | EffectBase, workers: tuple | None, environ: dict | None, revision: str, timing: ClusterTiming | None, policy: WorkerPolicy | None, outside: SimOutside | None, store: Callable | None, deployments: dict | None=None, runtime_env: RuntimeEnv | None=None, nodes: dict | None=None, *, notice_broker: MemoryBroker) -> _Program[Incomplete, object]:
    ...

def sim_cluster(system: System, scenario: Program | EffectBase, *, workers: tuple | None=None, environ: dict | None=None, revision: str='sim', start_ms: int=..., timing: ClusterTiming | None=None, policy: WorkerPolicy | None=None, outside: SimOutside | None=None, store: Callable | None=None, deployments: dict | None=None, runtime_env: RuntimeEnv | None=None, nodes: dict | None=None, notice_broker: MemoryBroker) -> _Program[Incomplete, object]:
    ...

def wall_sim_cluster(system: System, scenario: Program | EffectBase, *, workers: tuple | None=None, environ: dict | None=None, revision: str='sim', timing: ClusterTiming | None=None, policy: WorkerPolicy | None=None, outside: SimOutside | None=None, store: Callable | None=None, deployments: dict | None=None, runtime_env: RuntimeEnv | None=None, nodes: dict | None=None, notice_broker: MemoryBroker) -> _Program[Incomplete, object]:
    ...
