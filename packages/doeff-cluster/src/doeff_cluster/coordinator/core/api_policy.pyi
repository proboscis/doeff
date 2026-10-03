# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = api_policy.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import replace as replace
from traceback import extract_tb as extract_tb
from doeff_cluster.coordinator.intent.request_bodies import BodyMalformed as BodyMalformed
from doeff_cluster.coordinator.intent.request_bodies import BodyUnreadable as BodyUnreadable
from doeff_cluster.coordinator.intent.request_bodies import RequestBody as RequestBody
from doeff_cluster.shared.intent.protocol import ClusterTiming as ClusterTiming
from doeff_cluster.shared.intent.protocol import Request as Request
from doeff_cluster.shared.intent.protocol import PlainText as PlainText
from doeff_cluster.shared.intent.protocol import BodyInvalid as BodyInvalid
from doeff_cluster.coordinator.intent.cluster_model import ClusterState as ClusterState
from doeff_cluster.coordinator.intent.cluster_model import ErrorReply as ErrorReply
from doeff_cluster.coordinator.intent.cluster_model import TargetView as TargetView
from doeff_cluster.coordinator.intent.cluster_model import TaskDropped as TaskDropped
from doeff_cluster.coordinator.intent.cluster_model import BoardRead as BoardRead
from doeff_cluster.coordinator.intent.cluster_model import BoardEntryView as BoardEntryView
from doeff_cluster.coordinator.intent.cluster_model import ClusterNaming as ClusterNaming
from doeff_cluster.coordinator.intent.cluster_model import Fault as Fault
from doeff_cluster.coordinator.intent.cluster_model import RolloutStatus as RolloutStatus
from doeff_cluster.coordinator.intent.cluster_model import RolloutTarget as RolloutTarget
from doeff_cluster.coordinator.intent.cluster_model import StateReply as StateReply
from doeff_cluster.coordinator.intent.cluster_model import DeploymentSeen as DeploymentSeen
from doeff_cluster.coordinator.intent.cluster_model import DeploymentUnreadable as DeploymentUnreadable
from doeff_cluster.coordinator.intent.cluster_model import ServiceBody as ServiceBody
from doeff_cluster.coordinator.intent.cluster_model import LegacyJobs as LegacyJobs
from doeff_cluster.coordinator.core.cluster_rules import format_version_refusal as format_version_refusal
from doeff_cluster.coordinator.core.metrics_policy import record_metrics as record_metrics
from doeff_cluster.coordinator.core.metrics_policy import metrics_text as metrics_text
from doeff_cluster.coordinator.core.cluster_policy import reconcile as reconcile
from doeff_cluster.coordinator.core.cluster_policy import register_heartbeat as register_heartbeat
from doeff_cluster.coordinator.core.cluster_policy import heartbeat_reply as heartbeat_reply
from doeff_cluster.coordinator.core.cluster_policy import state_view as state_view
from doeff_cluster.coordinator.core.cluster_policy import submit_task as submit_task
from doeff_cluster.coordinator.core.cluster_policy import poll_task as poll_task
from doeff_cluster.coordinator.core.cluster_policy import absorb_task_result as absorb_task_result
from doeff_cluster.coordinator.core.cluster_policy import board_write as board_write
from doeff_cluster.coordinator.core.cluster_policy import note_liveness as note_liveness
from doeff_cluster.coordinator.core.cluster_policy import lease_write as lease_write
from doeff_cluster.coordinator.core.cluster_policy import other_generation_boot as other_generation_boot
from doeff_cluster.coordinator.core.cluster_policy import alive as alive
from doeff_cluster.coordinator.core.cluster_policy import remember_keep_marks as remember_keep_marks
from doeff_cluster.coordinator.core.cluster_policy import liveness_due as liveness_due
from doeff_cluster.coordinator.core.cluster_policy import task_due as task_due
from doeff_cluster.coordinator.core.cluster_policy import sweep_due as sweep_due
from doeff_cluster.coordinator.core.resource_policy import Refused as Refused
from doeff_cluster.coordinator.core.resource_policy import refuse as refuse
from doeff_cluster.coordinator.core.resource_policy import stamp as stamp
from doeff_cluster.coordinator.core.resource_policy import require_actor as require_actor
from doeff_cluster.coordinator.core.resource_policy import valid_actor as valid_actor
from doeff_cluster.coordinator.core.resource_policy import service_readiness as service_readiness
from doeff_cluster.coordinator.core.resource_policy import service_stopped as service_stopped
from doeff_cluster.coordinator.core.resource_policy import record_readiness as record_readiness
from doeff_cluster.coordinator.core.resource_policy import running_process as running_process
from doeff_cluster.coordinator.core.resource_policy import list_resources as list_resources
from doeff_cluster.coordinator.core.resource_policy import get_resource as get_resource
from doeff_cluster.coordinator.core.resource_policy import events_view as events_view
from doeff_cluster.coordinator.core.resource_policy import create_resource as create_resource
from doeff_cluster.coordinator.core.resource_policy import update_resource as update_resource
from doeff_cluster.coordinator.core.resource_policy import delete_resource as delete_resource
from doeff_cluster.coordinator.core.resource_policy import legacy_put_jobs as legacy_put_jobs
from doeff_cluster.coordinator.core.resource_policy import COORDINATOR as COORDINATOR
from doeff_cluster.coordinator.core.drain_policy import advance_drains as advance_drains
from doeff_cluster.coordinator.core.drain_policy import request_drain as request_drain
from doeff_cluster.coordinator.core.drain_policy import absorb_stopping as absorb_stopping
from doeff_cluster.coordinator.core.drain_policy import cancel_drain as cancel_drain
from doeff_cluster.coordinator.core.drain_policy import worker_view as worker_view
from doeff_cluster.coordinator.core.drain_policy import superseded_worker_view as superseded_worker_view
from doeff_cluster.coordinator.core.drain_policy import drains_view as drains_view
from doeff_cluster.coordinator.core.handoff_policy import watch_handoffs as watch_handoffs
from doeff_cluster.coordinator.core.handoff_policy import handoff_due as handoff_due
from doeff_cluster.coordinator.intent.cluster_model import HandoffPhase as HandoffPhase
from doeff_cluster.coordinator.core.detached_policy import Reply as Reply
from doeff_cluster.coordinator.core.detached_policy import submit_detached as submit_detached
from doeff_cluster.coordinator.core.detached_policy import detached_read as detached_read
from doeff_cluster.coordinator.core.detached_policy import cancel_detached as cancel_detached
from doeff_cluster.coordinator.core.detached_policy import release_detached as release_detached
from doeff_cluster.coordinator.core.rollout_policy import rollout_step as rollout_step
from doeff_cluster.coordinator.core.rollout_policy import rollout_targets as rollout_targets
from doeff_cluster.coordinator.core.rollout_policy import target_key as target_key
from doeff_cluster.coordinator.core.rollout_policy import deployment_owners as deployment_owners
from doeff_cluster.coordinator.core.rollout_policy import drift_status as drift_status
from doeff_cluster.coordinator.core.rollout_policy import action_due as action_due
from doeff_cluster.coordinator.core.rollout_policy import shift_clocks as shift_clocks
from doeff_cluster.coordinator.core.rollout_policy import TERMINAL_PHASES as TERMINAL_PHASES
from doeff_cluster.coordinator.core.warm_policy import warm_write as warm_write
from doeff_cluster.coordinator.core.warm_policy import warm_read as warm_read
from doeff_cluster.coordinator.core.program_policy import program_write as program_write
from doeff_cluster.coordinator.core.program_policy import program_read as program_read
from doeff_cluster.coordinator.core.program_policy import sweep_programs as sweep_programs
OBSERVATION_STALE_MS: int
ROLLOUT_ACTOR: str
TICK_MS: int
ROLLOUT_TICK_MS: int

def settle(before: ClusterState, after: ClusterState, actor: str, now: int, timing: ClusterTiming) -> _Program[ClusterState, object]:
    ...

def tick(state: ClusterState, now: int, timing: ClusterTiming) -> _Program[ClusterState, object]:
    ...

def placement_due(state: ClusterState, now: int, timing: ClusterTiming) -> _Program[int | None, object]:
    ...

def tick_due(state: ClusterState, now: int, timing: ClusterTiming) -> _Program[int | None, object]:
    ...
ALIVE_MARK_MS: int

def mark_alive(state: ClusterState, now: int) -> ClusterState:
    ...

def resume_after_downtime(state: ClusterState, now: int) -> tuple:
    ...

def target_view(state: ClusterState, target: RolloutTarget, status: RolloutStatus, now: int, timing: ClusterTiming) -> TargetView:
    ...

def ready_instances(state: ClusterState, worker: str, now: int, timing: ClusterTiming) -> dict:
    ...

def ready_instance(state: ClusterState, name: str, now: int, timing: ClusterTiming) -> str | None:
    ...

def deployment_reread_from(seen: DeploymentSeen | DeploymentUnreadable | None) -> int:
    ...

def deployments_to_observe(state: ClusterState, now: int) -> list:
    ...

def deployment_reread_due(state: ClusterState, now: int) -> _Program[int | None, object]:
    ...

def plan_rollouts(state: ClusterState, now: int, timing: ClusterTiming, naming: ClusterNaming=...) -> _Program[tuple, object]:
    ...

def scale_service(state: ClusterState, name: str, replicas: int) -> ClusterState:
    ...

def record_action(state: ClusterState, action: dict, ok: bool, error: str | None, now: int, result: int | None=None) -> ClusterState:
    ...

def loose_actor(request: Request) -> str:
    ...

def detached_reply(state: ClusterState, reply: Reply, request: Request, now: int, timing: ClusterTiming) -> _Program[tuple, object]:
    ...

def unknown_request(state: ClusterState, request: Request) -> tuple:
    ...

def respond_resources(state: ClusterState, request: Request, body: RequestBody | ServiceBody | LegacyJobs, parts: list, now: int, timing: ClusterTiming) -> _Program[tuple, object]:
    ...

def respond_observations(state: ClusterState, request: Request, body: object, parts: list, now: int, timing: ClusterTiming) -> tuple:
    ...

def quiet_heartbeat(before: ClusterState, heard: ClusterState, name: str, now: int, timing: ClusterTiming) -> bool:
    ...

def respond_legacy(state: ClusterState, request: Request, body: RequestBody | ServiceBody | LegacyJobs, parts: list, now: int, timing: ClusterTiming, settled: bool=False) -> _Program[tuple, object]:
    ...

def respond_workers(state: ClusterState, request: Request, body: RequestBody | ServiceBody | LegacyJobs, parts: list, now: int, timing: ClusterTiming) -> _Program[tuple, object]:
    ...

def respond_board(state: ClusterState, request: Request, body: RequestBody | ServiceBody | LegacyJobs, parts: list, now: int, timing: ClusterTiming) -> _Program[tuple, object]:
    ...

def respond_tasks(state: ClusterState, request: Request, body: RequestBody | ServiceBody | LegacyJobs, parts: list, now: int, timing: ClusterTiming) -> _Program[tuple, object]:
    ...

def respond_stores(state: ClusterState, request: Request, body: RequestBody | ServiceBody | LegacyJobs, parts: list, now: int, timing: ClusterTiming) -> _Program[tuple, object]:
    ...

def respond(state: ClusterState, request: Request, now: int, timing: ClusterTiming, body: RequestBody | ServiceBody | LegacyJobs | BodyUnreadable, settled: bool=False) -> _Program[tuple, object]:
    ...
