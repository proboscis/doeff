# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = cluster_policy.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from dataclasses import replace as replace
from itertools import groupby as groupby
from operator import attrgetter as attrgetter
from math import ceil as ceil
from doeff_cluster.shared.intent.job_model import JobSpec as JobSpec
from doeff_cluster.shared.intent.protocol import ClusterTiming as ClusterTiming
from doeff_cluster.shared.intent.protocol import Request as Request
from doeff_cluster.shared.intent.protocol import BodyInvalid as BodyInvalid
from doeff_cluster.shared.core.capabilities import capabilities_of as capabilities_of
from doeff_cluster.shared.core.capabilities import environ_pairs as environ_pairs
from doeff_cluster.coordinator.intent.cluster_model import ClusterJob as ClusterJob
from doeff_cluster.coordinator.intent.cluster_model import ErrorReply as ErrorReply
from doeff_cluster.coordinator.intent.cluster_model import TaskAccepted as TaskAccepted
from doeff_cluster.coordinator.intent.cluster_model import TaskProgress as TaskProgress
from doeff_cluster.coordinator.intent.cluster_model import TaskMissing as TaskMissing
from doeff_cluster.coordinator.intent.cluster_model import TaskResultTaken as TaskResultTaken
from doeff_cluster.coordinator.intent.cluster_model import BoardUsage as BoardUsage
from doeff_cluster.coordinator.intent.cluster_model import BoardWritten as BoardWritten
from doeff_cluster.coordinator.intent.cluster_model import BoardConflict as BoardConflict
from doeff_cluster.coordinator.intent.cluster_model import BoardRefused as BoardRefused
from doeff_cluster.coordinator.intent.cluster_model import WorkerInfo as WorkerInfo
from doeff_cluster.coordinator.intent.cluster_model import TaskOffer as TaskOffer
from doeff_cluster.coordinator.intent.cluster_model import WarmOffer as WarmOffer
from doeff_cluster.coordinator.intent.cluster_model import HeartbeatReply as HeartbeatReply
from doeff_cluster.coordinator.intent.cluster_model import ServiceView as ServiceView
from doeff_cluster.coordinator.intent.cluster_model import WorkerView as WorkerView
from doeff_cluster.coordinator.intent.cluster_model import StatusView as StatusView
from doeff_cluster.coordinator.intent.cluster_model import StateView as StateView
from doeff_cluster.coordinator.intent.cluster_model import BoardRow as BoardRow
from doeff_cluster.coordinator.intent.cluster_model import WorkerReport as WorkerReport
from doeff_cluster.coordinator.intent.cluster_model import GenerationOrder as GenerationOrder
from doeff_cluster.coordinator.intent.cluster_model import Placement as Placement
from doeff_cluster.coordinator.intent.cluster_model import ClusterState as ClusterState
from doeff_cluster.coordinator.intent.cluster_model import TaskRecord as TaskRecord
from doeff_cluster.coordinator.intent.cluster_model import EnvFailed as EnvFailed
from doeff_cluster.coordinator.intent.cluster_model import HandoffPhase as HandoffPhase
from doeff_cluster.coordinator.intent.cluster_model import UnplacedKind as UnplacedKind
from doeff_cluster.coordinator.intent.cluster_model import TaskUnplacedKind as TaskUnplacedKind
from doeff_cluster.coordinator.intent.cluster_model import WorkerLoad as WorkerLoad
from doeff_cluster.coordinator.intent.cluster_model import ACCEPTED_FORMATS as ACCEPTED_FORMATS
from doeff_cluster.coordinator.intent.cluster_model import PLACED_PHASES as PLACED_PHASES
from doeff_cluster.coordinator.intent.cluster_model import NodeLabelsSeen as NodeLabelsSeen
from doeff_cluster.coordinator.intent.cluster_model import KeepMark as KeepMark
from doeff_cluster.coordinator.intent.cluster_model import KnownExit as KnownExit
from doeff_cluster.shared.intent.due_model import DueAt as DueAt
from doeff_cluster.shared.intent.due_model import DueNow as DueNow
from doeff_cluster.shared.intent.due_model import DueNever as DueNever
from doeff_cluster.shared.core.due_policy import due_of_instants as due_of_instants
from doeff_cluster.coordinator.intent.worker_notices import WorkerBack as WorkerBack
from doeff_cluster.coordinator.intent.worker_notices import WorkerGone as WorkerGone
from doeff_hy.table import Table as Table
from doeff_cluster.coordinator.core.cluster_rules import component_versions_of as component_versions_of
from doeff_cluster.coordinator.core.cluster_rules import format_version_refusal as format_version_refusal
from doeff_cluster.coordinator.intent.request_bodies import LeaseBody as LeaseBody
from doeff_cluster.coordinator.intent.request_bodies import TaskResultBody as TaskResultBody
from doeff_cluster.coordinator.intent.request_bodies import BoardWrite as BoardWrite
from doeff_cluster.coordinator.intent.request_bodies import HeartbeatBody as HeartbeatBody
from doeff_cluster.coordinator.intent.request_bodies import EnvsReport as EnvsReport
from doeff_cluster.coordinator.intent.request_bodies import StatusRow as StatusRow
from doeff_cluster.coordinator.intent.request_bodies import TaskBody as TaskBody
from doeff_cluster.coordinator.core.cluster_rules import required_field as required_field
from doeff_cluster.coordinator.core.cluster_rules import int_field as int_field
from doeff_cluster.shared.intent.semaphore_model import SEMAPHORE_PREFIX as SEMAPHORE_PREFIX
from doeff_cluster.shared.core.lease_rules import lease_op as lease_op
from doeff_cluster.shared.core.lease_rules import semaphore_write_refusal as semaphore_write_refusal
from doeff_cluster.shared.core.lease_rules import semaphore_key as semaphore_key
from doeff_cluster.shared.core.lease_rules import drop_holders as drop_holders
from doeff_cluster.shared.core.lease_rules import lease_holder as lease_holder
from doeff_cluster.shared.core.lease_rules import holder_tokens_prefix as holder_tokens_prefix
from doeff_cluster.shared.core.board_rules import board_allows as board_allows
from doeff_cluster.shared.core.board_rules import board_ttl_refusal as board_ttl_refusal
from doeff import run as run
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnvInvalid as RuntimeEnvInvalid
from doeff_cluster.shared.core.runtime_env_rules import runtime_env_of_json as runtime_env_of_json
from doeff_cluster.shared.core.runtime_env_rules import env_key as env_key
from doeff_cluster.shared.core.runtime_env_rules import child_environ_refusal as child_environ_refusal
from doeff_cluster.shared.core.readiness_rules import readiness_refusal as readiness_refusal
from doeff_cluster.shared.core.readiness_rules import retired_lifetime_ms as retired_lifetime_ms
from doeff_cluster.shared.core.readiness_rules import retired_limit_of as retired_limit_of
from doeff_cluster.coordinator.core.program_policy import PROGRAM_GRACE_MS as PROGRAM_GRACE_MS
from doeff_cluster.coordinator.core.program_policy import program_refs as program_refs
JOB_ENTRY: str
MAX_EVENTS: int
BOARD_MAX_VALUE_BYTES: int
BOARD_MAX_ROWS: int
BOARD_MAX_BYTES: int
TASK_MAX_LEASE_SECONDS: int
TASK_MAX_OPEN: int

def declared_runtime_env(item: dict) -> str | None:
    ...

def spec_of_declaration(item: dict) -> JobSpec:
    ...
PROGRAM_SHA: Incomplete
OLD_RUN_KEYS: tuple[str, ...]
IMAGE_FOLLOW_KEYS: tuple[str, ...]

def raw_entry_refusal(item: dict) -> str:
    ...

def identity_hash(run: dict) -> str:
    ...

def program_row_refusal(item: dict) -> str | None:
    ...

def environ_refusal(environ: dict, declared: list) -> str | None:
    ...

def task_environ_refusal(environ: dict | list | tuple | str | int | float | bool | None, runtime: dict | list | tuple | str | int | float | bool | None) -> str | None:
    ...

def job_from_json(item: dict) -> ClusterJob:
    ...

def job_to_json(job: ClusterJob) -> dict:
    ...

def boot_at_of(body: dict) -> int | None:
    ...

def resource_version_of(state: ClusterState, key: str) -> int | None:
    ...

def status_row_to_json(row: StatusRow) -> dict:
    ...

def liveness_deadline(worker: WorkerInfo, window_ms: int) -> int:
    ...

def alive(now: int, worker: WorkerInfo, window_ms: int) -> bool:
    ...

def silent_names(state: ClusterState, now: int, timing: ClusterTiming) -> frozenset:
    ...

def note_liveness(state: ClusterState, now: int, timing: ClusterTiming) -> ClusterState:
    ...

def liveness_moves(before: ClusterState, after: ClusterState, timing: ClusterTiming) -> _Program[tuple, object]:
    ...

def liveness_now(state: ClusterState, timing: ClusterTiming) -> _Program[tuple, object]:
    ...

def placeable(needs: tuple, worker: WorkerInfo) -> bool:
    ...

def named_capabilities(provides: tuple | list | None, exclusive: tuple | list | None, old_labels: bool, what: str) -> tuple:
    ...

def request_needs(body: dict, what: str) -> tuple:
    ...

def needs_named(needs: dict | list | tuple | str | int | float | bool | None, requires: dict | list | tuple | str | int | float | bool | None, what: str) -> tuple:
    ...

def task_body_refusal(state: ClusterState, body: TaskBody) -> str | None:
    ...

def program_versions(state: ClusterState, sha: str) -> tuple:
    ...

def needs_refusal(needs: dict | list | tuple | str | int | float | bool | None, requires: dict | list | tuple | str | int | float | bool | None) -> str | None:
    ...

def eligible(job: ClusterJob, worker: WorkerInfo) -> bool:
    ...
LIVE_PHASES: set[str]

def service_rows(now: int, state: ClusterState, name: str, timing: ClusterTiming) -> tuple:
    ...

def still_live_somewhere(now: int, state: ClusterState, name: str, timing: ClusterTiming) -> bool:
    ...

def draining_workers(state: ClusterState, now: int) -> frozenset:
    ...

def can_take(now: int, state: ClusterState, job: ClusterJob, w: WorkerInfo, load: dict, timing: ClusterTiming, draining: frozenset | None=None) -> bool:
    ...
RETIRED_BOOTS_KEPT: int

def generation_order(current: WorkerInfo | None, boot: str | None, boot_at: int | None) -> GenerationOrder:
    ...

def retired_with(retired: tuple, boot: str) -> tuple:
    ...

def superseded_boot(state: ClusterState, name: str, boot: str | None) -> bool:
    ...

def other_generation_boot(state: ClusterState, name: str, boot: str | None) -> bool:
    ...

def retired_after(previous: WorkerInfo | None, boot: str | None) -> tuple:
    ...

def absorb_boot(state: ClusterState, name: str, boot: str | None) -> ClusterState:
    ...

def generation_holder_prefixes(report: WorkerReport | None) -> _Program[tuple, object]:
    ...

def holders_dropped(row: BoardRow, prefixes: tuple) -> _Program[dict | None, object]:
    ...

def board_without_generation_holders(board: dict, report: WorkerReport | None) -> _Program[dict, object]:
    ...

def load_of(state: ClusterState, placements: dict) -> dict:
    ...

def job_room_of(worker: WorkerInfo, load: dict) -> _Program[int, object]:
    ...

def task_room_of(worker: WorkerInfo, load: dict) -> _Program[int, object]:
    ...

def active_jobs(state: ClusterState) -> tuple:
    ...

def hyx_movableXquestion_markX(now: int, state: ClusterState, job: ClusterJob, holder: str, load: dict, timing: ClusterTiming, draining: frozenset) -> _Program[bool, object]:
    ...

def keep_marked(now: int, state: ClusterState, worker: str, timing: ClusterTiming) -> _Program[frozenset, object]:
    ...

def keep_mark_of(marks: tuple, job: str) -> _Program[KeepMark | None, object]:
    ...

def held_placements(now: int, state: ClusterState, timing: ClusterTiming) -> _Program[dict, object]:
    ...

def released_keep_marks(marks: tuple, worker: str, held: tuple | None) -> _Program[tuple, object]:
    ...

def remember_keep_marks(state: ClusterState, worker: str, boot: str | None, held: tuple | None, reply: HeartbeatReply, now: int) -> _Program[ClusterState, object]:
    ...

def sweep_keep_marks(state: ClusterState, now: int, timing: ClusterTiming) -> _Program[ClusterState, object]:
    ...

def place_jobs(now: int, state: ClusterState, timing: ClusterTiming) -> _Program[dict, object]:
    ...

def jobs_for(state: ClusterState, worker: str, ready_instances: dict | None=None) -> tuple:
    ...

def tools_satisfy(task: TaskRecord, worker: WorkerInfo) -> bool:
    ...

def tools_cover(declared: dict | None, worker: WorkerInfo) -> bool:
    ...

def _root_key(declared_text: str, platform: str) -> Incomplete:
    ...

def root_key_on(declared: dict, worker: WorkerInfo) -> str:
    ...

def env_ready_on(declared: dict | None, worker: WorkerInfo) -> bool:
    ...

def env_room_on(task: TaskRecord, worker: WorkerInfo) -> bool:
    ...

def can_run_task(task: TaskRecord, worker: WorkerInfo) -> bool:
    ...

def versions_note(task: TaskRecord, state: ClusterState, now: int, timing: ClusterTiming) -> str:
    ...

def unplaced_kind(now: int, state: ClusterState, job: ClusterJob, timing: ClusterTiming) -> UnplacedKind:
    ...

def unplaced_text(kind: UnplacedKind, job: ClusterJob) -> str:
    ...

def unplaced_jobs(now: int, state: ClusterState, timing: ClusterTiming) -> dict:
    ...

def task_unplaced_text(kind: TaskUnplacedKind) -> _Program[str, object]:
    ...
DETACHED_TERMINAL: tuple[str, ...]
ENV_RETRIES: int

def end_detached(task: TaskRecord, phase: str, now: int, detail: str, result: str | None=None) -> TaskRecord:
    ...

def settle_detached(task: TaskRecord, now: int, lapsed: bool) -> _Program[TaskRecord | None, object]:
    ...

def unplaceable_phase(task: TaskRecord, state: ClusterState, now: int, timing: ClusterTiming) -> str:
    ...

def task_lapse_at(task: TaskRecord, now: int) -> _Program[int | None, object]:
    ...

def wait_lapse_at(capable: list, timing: ClusterTiming) -> _Program[int | None, object]:
    ...

def place_tasks(now: int, state: ClusterState, placements: dict, timing: ClusterTiming) -> _Program[dict, object]:
    ...

def same_boot(task: TaskRecord, boot: str | None) -> bool:
    ...

def end_env_failed(task: TaskRecord, now: int, detail: str) -> TaskRecord:
    ...

def absorb_env_failure(task: TaskRecord, worker: str, status: StatusRow, now: int) -> TaskRecord:
    ...

def tasks_for(state: ClusterState, worker: str, boot: str | None=None) -> tuple:
    ...

def task_finished(task: TaskRecord, now: int, detail: str, result: str | None) -> TaskRecord:
    ...

def absorb_detached_report(task: TaskRecord, status: StatusRow, now: int) -> TaskRecord:
    ...

def absorb_task_reports(state: ClusterState, worker: str, statuses: tuple, now: int, boot: str | None=None, stopping: bool=False) -> dict:
    ...

def absorb_task_result(state: ClusterState, id: str, body: TaskResultBody, now: int) -> _Program[tuple, object]:
    ...

def renew_detached(tasks: dict, worker: str, boot: str | None, now: int) -> dict:
    ...

def sweep_board(state: ClusterState, now: int) -> _Program[ClusterState, object]:
    ...

def forget_silent_workers(state: ClusterState, now: int, timing: ClusterTiming) -> _Program[ClusterState, object]:
    ...

def sweep_drains(state: ClusterState, now: int) -> _Program[ClusterState, object]:
    ...

def sweep_warms(state: ClusterState, now: int) -> _Program[ClusterState, object]:
    ...

def cold_starts(before: dict, after: dict) -> _Program[int, object]:
    ...

def liveness_due(state: ClusterState, now: int, timing: ClusterTiming) -> _Program[DueAt | DueNow | DueNever, object]:
    ...

def task_due(state: ClusterState, now: int, timing: ClusterTiming) -> _Program[DueAt | DueNow | DueNever, object]:
    ...

def sweep_due(state: ClusterState, now: int, timing: ClusterTiming) -> _Program[DueAt | DueNow | DueNever, object]:
    ...

def reconcile(now: int, given: ClusterState, timing: ClusterTiming) -> _Program[ClusterState, object]:
    ...

def durable_changed(before: ClusterState, after: ClusterState) -> bool:
    ...

def board_changes(before: ClusterState, after: ClusterState) -> list:
    ...

def hyx_text_mapXquestion_markX(value: dict | list | str | int | float | bool | None) -> bool:
    ...

def known_exits_after(previous: WorkerInfo | None, rows: tuple, order: GenerationOrder, boot_at: int | None, declared: frozenset) -> _Program[tuple, object]:
    ...

def rows_with_known_exits(rows: tuple, known: tuple) -> _Program[tuple, object]:
    ...

def worker_report(rows: tuple, now: int, endpoint: str | None, known: tuple) -> _Program[WorkerReport, object]:
    ...

def register_heartbeat(given: ClusterState, body: HeartbeatBody, now: int) -> _Program[ClusterState, object]:
    ...

def absorb_superseded_heartbeat(state: ClusterState, name: str, boot: str, statuses: tuple, now: int) -> ClusterState:
    ...
ADOPTABLE_PHASES: set[str]

def task_number(prefix: str, id: str) -> int | None:
    ...

def task_id(state: ClusterState) -> str:
    ...

def fresh_task_prefix(now: int) -> str:
    ...

def adopted_task(state: ClusterState, worker: str, boot: str | None, status: StatusRow, now: int) -> TaskRecord | None:
    ...

def adopt_running_detached(state: ClusterState, worker: str, boot: str | None, statuses: tuple, now: int) -> ClusterState:
    ...

def promote_prepared(tasks: dict, worker: WorkerInfo) -> dict:
    ...

def warms_for(state: ClusterState, worker: str, now: int) -> tuple:
    ...

def heartbeat_reply(state: ClusterState, name: str, timing: ClusterTiming, ready_instances: dict | None=None, now: int=0, boot: str | None=None, statuses: tuple | None=None) -> _Program[HeartbeatReply, object]:
    ...

def running_names(statuses: tuple) -> frozenset:
    ...

def superseded_reply(state: ClusterState, name: str, boot: str, statuses: tuple, timing: ClusterTiming, ready_instances: dict | None=None) -> HeartbeatReply:
    ...

def state_view(state: ClusterState, now: int, timing: ClusterTiming) -> _Program[StateView, object]:
    ...

def runtime_env_refusal(body: dict) -> str | None:
    ...

def runtime_env_value_refusal(value: dict | list | tuple | str | int | float | bool | None) -> str | None:
    ...

def submit_task(state: ClusterState, body: TaskBody, now: int, owner: str | None=None) -> _Program[tuple, object]:
    ...

def poll_task(state: ClusterState, id: str, now: int) -> _Program[tuple, object]:
    ...

def lease_write(state: ClusterState, name: str, body: LeaseBody, now: int) -> _Program[tuple, object]:
    ...

def value_size(value: dict | list | str | int | float | bool | None) -> int:
    ...

def board_usage(state: ClusterState) -> BoardUsage:
    ...

def board_capacity_refusal(state: ClusterState, key: str, size: int) -> str | None:
    ...

def written_value(write: BoardWrite) -> object:
    ...

def board_write(state: ClusterState, key: str, write: BoardWrite, now: int=0) -> _Program[tuple, object]:
    ...

def nodes_to_follow(state: ClusterState) -> _Program[tuple, object]:
    ...

def derived_capabilities(labels: Table[str], table: tuple) -> tuple:
    ...

def with_derived_capabilities(state: ClusterState, table: tuple) -> ClusterState:
    ...
