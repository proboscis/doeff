"""coordinator/intent/cluster_model.hy の公開面の型(型検査のための宣言 — 実行時は cluster_model.hy を読む・#2447)。

cluster_model.hy は Hy の module なので、pyright は中を読めず、名が全部 Unknown になる。coordinator の状態と Rollout の宣言の型
(ClusterState・RolloutSpec ほか)を名指す使い手(使い手の repo の模擬の世界の検)の strict に、書き手に直せない赤
(Type of "RolloutSpec" is unknown ほか)が出た。ここで型を宣言する(worker_model.pyi・request_bodies.pyi と同じ形)。

- dataclass の class(ClusterJob・WorkerInfo ほか)は凍った dataclass。defrecord(ProgramRow・RolloutSpec ほか)は凍った・
  キーワード引数だけの dataclass。欄の型は cluster_model.hy の注記。
- defenum(GenerationOrder ほか)は StrEnum。値は defenum の綴り(名の小文字と - か、明示の値)。
- effect(CoordinatorFault・Persist)は凍った dataclass の EffectBase[None]。
- 型の宣言がまだ無い Hy の module の型(shared/intent/protocol の ClusterTiming・Request・NextRequests と、coordinator/intent/
  request_bodies の StatusRow)は object と書く。IdleNextRequests は実行時は NextRequests の子 class だが、ここでは idle の欄だけを
  宣言する。それらの module に宣言を置いたら実物の型へ置き換える。
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

from doeff import EffectBase

from doeff_cluster.shared.intent.job_model import JobSpec

MODULE_TAGS: dict[str, str]

class ComponentVersion(NamedTuple):
    component: str
    version: str

@dataclass(frozen=True)
class ClusterJob:
    spec: JobSpec
    needs: tuple[str, ...] = ()
    pin: str | None = None
    run: dict[str, object] | None = None
    replicas: int = 1
    readiness: dict[str, object] | None = None
    owner: str | None = None
    update: str = "recreate"

class GenerationOrder(StrEnum):
    CURRENT = "current"
    OLDER = "older"
    NEWER = "newer"

@dataclass(frozen=True)
class WorkerInfo:
    name: str
    provides: tuple[str, ...]
    capacity: int
    last_seen_ms: int
    versions: tuple[ComponentVersion, ...] = ()
    boot: str | None = None
    tools: tuple[ComponentVersion, ...] = ()
    platform: str = ""
    env_ready: frozenset[str] = frozenset()
    env_preparing: frozenset[str] = frozenset()
    env_failed: tuple[EnvFailed, ...] = ()
    env_capacity: str = "ok"
    retired: tuple[str, ...] = ()
    boot_at: int | None = None
    exclusive: tuple[str, ...] = ()
    node: str = ""
    derived: tuple[str, ...] = ()

@dataclass(frozen=True)
class EnvFailed:
    key: str
    kind: str
    detail: str
    retryable: bool

@dataclass(frozen=True)
class WarmEntry:
    key: str
    runtime_env: dict[str, object]
    needs: tuple[str, ...]
    until_ms: int
    holder: str

@dataclass(frozen=True, kw_only=True)
class ProgramRow:
    blob: str
    versions: dict[str, str]
    put_ms: int

@dataclass(frozen=True, kw_only=True)
class ResourceMeta:
    resource_version: int
    generation: int
    created_by: str
    created_ms: int
    updated_by: str
    updated_ms: int

@dataclass(frozen=True, kw_only=True)
class BoardRow:
    value: object
    version: int
    expires_ms: int | None
    size: int

@dataclass(frozen=True, kw_only=True)
class RolloutTarget:
    kind: str
    name: str
    namespace: str | None = None
    replicas: int | None = None
    dry_run: bool = False

@dataclass(frozen=True, kw_only=True)
class RolloutSpec:
    from_target: RolloutTarget
    to_target: RolloutTarget
    owner: str | None
    ready_timeout_seconds: int | float
    stop_timeout_seconds: int | float
    observe_seconds: int | float
    fail_after_seconds: int | float
    rollback_timeout_seconds: int | float
    mark_deployment: bool
    abort: bool

@dataclass(frozen=True, kw_only=True)
class RolloutHistory:
    phase: str
    at: int
    reason: str

@dataclass(frozen=True, kw_only=True)
class RolloutStuck:
    step: str
    reason: str
    since_ms: int

@dataclass(frozen=True, kw_only=True)
class RolloutDrift:
    deployment: str
    expected: int
    observed: int
    since_ms: int
    note: str

@dataclass(frozen=True, kw_only=True)
class RolloutStatus:
    phase: str = "Pending"
    phase_since_ms: int | None = None
    reason: str | None = None
    history: tuple[RolloutHistory, ...] = ()
    created_ms: int | None = None
    started_ms: int | None = None
    from_replicas: int | None = None
    stopped_old_ms: int | None = None
    not_ready_since_ms: int | None = None
    unknown_since_ms: int | None = None
    completed_ms: int | None = None
    rollback_step: str | None = None
    failure: str | None = None
    restored_old_ms: int | None = None
    stuck: RolloutStuck | None = None
    stuck_cleared_ms: int | None = None
    last_action: dict[str, object] | None = None
    simulated: dict[str, int] | None = None
    marked_deployment: str | None = None
    drift: RolloutDrift | None = None
    drift_resolved_ms: int | None = None

@dataclass(frozen=True, kw_only=True)
class RolloutRow:
    spec: RolloutSpec
    status: RolloutStatus

@dataclass(frozen=True, kw_only=True)
class AuditEvent:
    seq: int
    at: int
    actor: str
    verb: str
    kind: str
    name: str
    from_version: int | None
    to_version: int | None
    generation: int | None
    changes: dict[str, object]

@dataclass(frozen=True, kw_only=True)
class EventsView:
    revision: int
    seq: int
    events: tuple[AuditEvent, ...]

@dataclass(frozen=True, kw_only=True)
class ServiceView:
    job: ClusterJob
    resource_version: int | None

@dataclass(frozen=True, kw_only=True)
class WorkerView:
    info: WorkerInfo
    silent_ms: int
    live: bool
    draining: bool

@dataclass(frozen=True, kw_only=True)
class StatusView:
    report: WorkerReport
    stale: bool

@dataclass(frozen=True, kw_only=True)
class StateView:
    now: int
    services: tuple[ServiceView, ...]
    workers: tuple[WorkerView, ...]
    placements: dict[str, Placement]
    unplaced: dict[str, str]
    statuses: dict[str, StatusView]
    tasks: tuple[TaskRecord, ...]
    board_keys: int
    surges: dict[str, Placement]
    events: tuple[object, ...]
    revision: int

class DrainPhase(StrEnum):
    DRAINING = "Draining"
    DRAINED = "Drained"
    BLOCKED = "Blocked"

@dataclass(frozen=True, kw_only=True)
class DrainProgress:
    worker: str
    boot: str | None
    superseded: bool
    since_ms: int | None
    until_ms: int | None
    actor: str | None
    phase: DrainPhase
    remaining: tuple[str, ...]
    moving: dict[str, str]
    blocked: dict[str, str]
    moving_ready: dict[str, str]

@dataclass(frozen=True, kw_only=True)
class WorkerDrainView:
    info: WorkerInfo
    alive: bool
    silent_ms: int
    superseded: bool
    drain: DrainProgress | None
    ready: bool

@dataclass(frozen=True, kw_only=True)
class StateReply:
    view: StateView
    audit: tuple[AuditEvent, ...]
    drains: dict[str, DrainProgress]

@dataclass(frozen=True, kw_only=True)
class WorkerReport:
    at: int
    endpoint: str | None
    jobs: tuple[object, ...]

@dataclass(frozen=True, kw_only=True)
class ServiceBody:
    name: str | None
    job: ClusterJob
    owner: object
    resource_version: int | None

@dataclass(frozen=True, kw_only=True)
class LegacyJobRow:
    name: str
    version: object
    owner: object
    job: ClusterJob
    replicas_given: bool
    readiness_given: bool

@dataclass(frozen=True, kw_only=True)
class LegacyJobs:
    rows: tuple[LegacyJobRow, ...]
    actor: str | None

@dataclass(frozen=True, kw_only=True)
class RefusedJob:
    name: str
    row: dict[str, object]
    reason: str

@dataclass(frozen=True)
class Placement:
    job: str
    worker: str
    generation: int
    since_ms: int

@dataclass(frozen=True)
class Drain:
    worker: str
    since_ms: int
    until_ms: int
    boot: str | None = None
    actor: str = ""

class HandoffPhase(StrEnum):
    WAITING = "WaitingReady"
    ABANDONED = "Abandoned"

@dataclass(frozen=True, kw_only=True)
class HandoffWatch:
    declaration: str
    since_ms: int
    phase: HandoffPhase = HandoffPhase.WAITING
    abandoned_ms: int | None = None
    reason: str = ""
    last_report: str | None = None
    def to_json(self) -> dict[str, object]: ...
    def status_json(self, timeout_ms: int) -> dict[str, object]: ...

class UnplacedKind(StrEnum):
    WAITING_PREVIOUS_HOLDER = "waiting-previous-holder"
    NO_ELIGIBLE_WORKER = "no-eligible-worker"
    NO_ROOM = "no-room"

class NotReadyKind(StrEnum):
    NO_DECLARATION = "no-declaration"
    NO_REPLICAS = "no-replicas"
    WAITING_PREVIOUS_HOLDER = "waiting-previous-holder"
    NO_ELIGIBLE_WORKER = "no-eligible-worker"
    NO_ROOM = "no-room"
    CARRIER_SILENT = "carrier-silent"
    NOT_RUNNING = "not-running"
    REVISION_MISMATCH = "revision-mismatch"
    NO_INSTANCE = "no-instance"
    SPEC_MISMATCH = "spec-mismatch"
    PLACEMENT_MISMATCH = "placement-mismatch"

class VersionState(StrEnum):
    CURRENT = "Current"
    UPDATING = "Updating"
    BLOCKED = "Blocked"
    STOPPED = "Stopped"
    UNKNOWN = "Unknown"

@dataclass(frozen=True, kw_only=True)
class VersionVerdict:
    state: VersionState
    reason: str

@dataclass(frozen=True, kw_only=True)
class LiveProcess:
    revision: str | None
    retired: bool

@dataclass(frozen=True, kw_only=True)
class ServiceObserved:
    ready_reason: str
    last_readiness: dict[str, object] | None
    process: object | None
    version: VersionVerdict
    running: tuple[LiveProcess, ...]

@dataclass(frozen=True, kw_only=True)
class WorkerObserved:
    silent_ms: int
    alive: bool

@dataclass(frozen=True, kw_only=True)
class TaskObserved:
    task: TaskRecord

@dataclass(frozen=True, kw_only=True)
class RolloutObserved:
    observed: dict[str, dict[str, object] | None]

@dataclass(frozen=True, kw_only=True)
class ResourceView:
    kind: str
    name: str
    meta: ResourceMeta | None
    spec: dict[str, object]
    status: dict[str, object]
    observed: ServiceObserved | WorkerObserved | TaskObserved | RolloutObserved | None

@dataclass(frozen=True, kw_only=True)
class ResourceList:
    kind: str
    revision: int
    items: tuple[ResourceView, ...]

@dataclass(frozen=True, kw_only=True)
class RowConflict:
    name: str
    message: str
    current: int | None = None

@dataclass(frozen=True, kw_only=True)
class ErrorReply:
    message: str
    current: int | None = None
    conflicts: tuple[RowConflict, ...] | None = None
    open: int | None = None
    fault: bool = False

@dataclass(frozen=True)
class ClusterNaming:
    owner_annotation: str = "doeff-cluster/replicas-owned-by"
    owner_scope: str = "doeff-cluster"
    node_capabilities: tuple[tuple[str, str, str], ...] = ...

ACCEPTED_FORMATS: tuple[int, ...]

@dataclass(frozen=True)
class TaskRecord:
    id: str
    name: str
    program: str | None
    revision: str
    versions: tuple[ComponentVersion, ...]
    needs: tuple[str, ...]
    lease_ms: int
    lease_until_ms: int
    submitted_ms: int
    phase: str = "queued"
    worker: str | None = None
    result: str | None = None
    detail: str = ""
    started_ms: int | None = None
    finished_ms: int | None = None
    detached: bool = False
    key: str | None = None
    boot: str | None = None
    retain_ms: int = 0
    runtime_env: dict[str, object] | None = None
    env_attempts: int = 0
    avoid: tuple[str, ...] = ()
    failure_kind: str = ""
    retryable: bool = False
    environ: tuple[tuple[str, str], ...] = ()

PLACED_PHASES: frozenset[str]
ENDED_PHASES: frozenset[str]

@dataclass(frozen=True)
class ClusterState:
    jobs: tuple[ClusterJob, ...] = ()
    workers: dict[str, WorkerInfo] = ...
    placements: dict[str, Placement] = ...
    tasks: dict[str, TaskRecord] = ...
    next_task: int = 1
    task_prefix: str = "t"
    board: dict[str, BoardRow] = ...
    statuses: dict[str, WorkerReport] = ...
    events: tuple[object, ...] = ()
    meta: dict[str, ResourceMeta] = ...
    revision: int = 0
    audit: tuple[AuditEvent, ...] = ()
    audit_seq: int = 0
    rollouts: dict[str, RolloutRow] = ...
    readiness: dict[str, object] = ...
    metrics: dict[str, object] = ...
    deployments: dict[str, dict[str, object]] = ...
    nodes: dict[str, dict[str, object]] = ...
    derivable: frozenset[str] = frozenset()
    refused: dict[str, RefusedJob] = ...
    programs: dict[str, ProgramRow] = ...
    started_ms: int = 0
    rollout_tick_ms: int = 0
    alive_ms: int = 0
    seen_marks: dict[str, int] = ...
    drains: dict[str, Drain] = ...
    surges: dict[str, Placement] = ...
    warms: dict[str, WarmEntry] = ...
    env_cold_starts: int = 0
    handoffs: dict[str, HandoffWatch] = ...
    silent: frozenset[str] = frozenset()

@dataclass(frozen=True)
class Fault:
    method: str
    path: str
    error_type: str
    message: str
    where: str

@dataclass(frozen=True, kw_only=True)
class TaskOffer:
    id: str
    name: str
    revision: str
    versions: tuple[ComponentVersion, ...]
    program: str | None
    detached: bool
    key: str | None
    lease_ms: int
    retain_ms: int
    needs: tuple[str, ...]
    runtime_env: dict[str, object] | None
    environ: tuple[tuple[str, str], ...]

@dataclass(frozen=True, kw_only=True)
class WarmOffer:
    key: str
    runtime_env: dict[str, object]

@dataclass(frozen=True, kw_only=True)
class HeartbeatReply:
    jobs: tuple[JobSpec, ...]
    tasks: tuple[TaskOffer, ...]
    warm: tuple[WarmOffer, ...]
    timing: object
    draining: bool
    superseded: bool
    formats: tuple[int, ...]
    revision: int

@dataclass(frozen=True)
class Watcher:
    request: object
    after: int
    deadline_ms: int
    worker: str | None = None
    boot: str | None = None
    mark: HeartbeatReply | None = None

@dataclass(frozen=True)
class WatchRefusal:
    request: object
    reason: str

@dataclass(frozen=True)
class WatchAnswer:
    revision: int
    changed: bool

@dataclass(frozen=True)
class WatchStep:
    answer: WatchAnswer | None
    watcher: Watcher

@dataclass(frozen=True)
class IdleProbe:
    state: ClusterState
    timing: object
    naming: ClusterNaming
    wake_ms: int | None = None

@dataclass(frozen=True)
class IdleNextRequests:
    idle: IdleProbe | None = None

@dataclass(frozen=True)
class CoordinatorFault(EffectBase[None]):
    fault: Fault

@dataclass(frozen=True)
class SaveState(EffectBase[None]):
    before: ClusterState
    after: ClusterState
