# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = replies.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from doeff_cluster._shared.intent.protocol import Reply as Reply
from doeff_cluster._shared.intent.protocol import PlainText as PlainText
from dataclasses import asdict as asdict
from collections.abc import Callable as Callable
from collections.abc import Iterable as Iterable
from collections.abc import Mapping as Mapping
from doeff_cluster._shared.intent.job_model import JobSpec as JobSpec
from doeff_cluster._coordinator.intent.cluster_model import EventsView as EventsView
from doeff_cluster._coordinator.intent.cluster_model import StateReply as StateReply
from doeff_cluster._coordinator.intent.cluster_model import StateView as StateView
from doeff_cluster._coordinator.intent.cluster_model import HeartbeatReply as HeartbeatReply
from doeff_cluster._coordinator.intent.cluster_model import TaskOffer as TaskOffer
from doeff_cluster._coordinator.intent.cluster_model import DrainPhase as DrainPhase
from doeff_cluster._coordinator.intent.cluster_model import DrainProgress as DrainProgress
from doeff_cluster._coordinator.intent.cluster_model import WorkerDrainView as WorkerDrainView
from doeff_cluster._coordinator.intent.cluster_model import ServiceObserved as ServiceObserved
from doeff_cluster._coordinator.intent.cluster_model import UnplacedView as UnplacedView
from doeff_cluster._coordinator.intent.cluster_model import WorkerObserved as WorkerObserved
from doeff_cluster._coordinator.intent.cluster_model import TaskObserved as TaskObserved
from doeff_cluster._coordinator.intent.cluster_model import RolloutObserved as RolloutObserved
from doeff_cluster._coordinator.intent.cluster_model import ResourceView as ResourceView
from doeff_cluster._coordinator.intent.cluster_model import ResourceList as ResourceList
from doeff_cluster._coordinator.intent.cluster_model import VersionVerdict as VersionVerdict
from doeff_cluster._coordinator.intent.cluster_model import ErrorReply as ErrorReply
from doeff_cluster._coordinator.intent.cluster_model import RowConflict as RowConflict
from doeff_cluster._coordinator.intent.cluster_model import BoardUsage as BoardUsage
from doeff_cluster._coordinator.intent.cluster_model import BoardRead as BoardRead
from doeff_cluster._coordinator.intent.cluster_model import BoardWritten as BoardWritten
from doeff_cluster._coordinator.intent.cluster_model import BoardConflict as BoardConflict
from doeff_cluster._coordinator.intent.cluster_model import BoardRefused as BoardRefused
from doeff_cluster._coordinator.intent.cluster_model import TaskRecord as TaskRecord
from doeff_cluster._coordinator.intent.cluster_model import TaskAccepted as TaskAccepted
from doeff_cluster._coordinator.intent.cluster_model import TaskProgress as TaskProgress
from doeff_cluster._coordinator.intent.cluster_model import TaskMissing as TaskMissing
from doeff_cluster._coordinator.intent.cluster_model import TaskResultTaken as TaskResultTaken
from doeff_cluster._coordinator.intent.cluster_model import TaskDropped as TaskDropped
from doeff_cluster._coordinator.intent.cluster_model import DetachedSubmitted as DetachedSubmitted
from doeff_cluster._coordinator.intent.cluster_model import DetachedProgress as DetachedProgress
from doeff_cluster._coordinator.intent.cluster_model import DetachedUnknown as DetachedUnknown
from doeff_cluster._coordinator.intent.cluster_model import DetachedWarming as DetachedWarming
from doeff_cluster._coordinator.intent.cluster_model import DetachedCancelled as DetachedCancelled
from doeff_cluster._coordinator.intent.cluster_model import DetachedReleased as DetachedReleased
from doeff_cluster._coordinator.intent.cluster_model import ProgramRow as ProgramRow
from doeff_cluster._coordinator.intent.cluster_model import ProgramStored as ProgramStored
from doeff_cluster._coordinator.intent.cluster_model import DeploymentSeen as DeploymentSeen
from doeff_cluster._coordinator.intent.cluster_model import DeploymentUnreadable as DeploymentUnreadable
from doeff_cluster._coordinator.intent.cluster_model import ReadinessReport as ReadinessReport
from doeff_cluster._shared.intent.warm_model import WarmState as WarmState
from doeff_cluster._shared.core.warm_rules import hyx_warm_state_XgreaterHthan_signXjson as hyx_warm_state_XgreaterHthan_signXjson
from doeff_hy._wire import dump as dump
from doeff_cluster._shared.intent.semaphore_model import LeaseAnswer as LeaseAnswer
from doeff_cluster._coordinator.core.cluster_policy import job_to_json as job_to_json
from doeff_cluster._coordinator.core.cluster_policy import status_row_to_json as status_row_to_json
from doeff_cluster._coordinator.protocol.state_json import audit_event_to_json as audit_event_to_json
from doeff_cluster._shared.core.detached_rules import group_limit_spec as group_limit_spec
from doeff_cluster._shared.core.detached_rules import task_limits_json as task_limits_json
from doeff_cluster._coordinator.core.resource_policy import needs_override_spec as needs_override_spec
from doeff_cluster._shared.intent.detached_model import DetachedOutbox as DetachedOutbox
from doeff_cluster._shared.intent.detached_model import DetachedOutboxAcked as DetachedOutboxAcked
from doeff_cluster._coordinator.protocol.cluster_json import ended_to_json as ended_to_json

def spelled_each(spell: Callable, items: Iterable) -> _Program[list, object]:
    ...

def task_summary(task: TaskRecord) -> _Program[dict, object]:
    ...

def spec_json(spec: JobSpec) -> _Program[dict, object]:
    ...

def task_offer_json(offer: TaskOffer) -> _Program[dict, object]:
    ...

def heartbeat_reply_json(reply: HeartbeatReply) -> _Program[dict, object]:
    ...

def state_view_json(view: StateView) -> _Program[dict, object]:
    ...

def state_view_body(view: StateView) -> _Program[dict, object]:
    ...

def unplaced_view_json(view: UnplacedView) -> _Program[dict, object]:
    ...

def drain_progress_json(drain: DrainProgress) -> _Program[dict, object]:
    ...

def worker_drain_view_json(view: WorkerDrainView) -> _Program[dict, object]:
    ...

def version_json(verdict: VersionVerdict, live: tuple) -> _Program[dict, object]:
    ...

def deployment_observation_json(seen: DeploymentSeen | DeploymentUnreadable | None) -> _Program[Mapping[str, object] | None, object]:
    ...

def readiness_report_json(report: ReadinessReport | None) -> _Program[Mapping[str, object] | None, object]:
    ...

def observed_json(observed: ServiceObserved | WorkerObserved | TaskObserved | RolloutObserved | None) -> _Program[dict, object]:
    ...

def resource_view_json(view: ResourceView) -> _Program[dict, object]:
    ...

def row_conflict_json(conflict: RowConflict) -> _Program[dict, object]:
    ...

def error_reply_json(reply: ErrorReply) -> _Program[dict, object]:
    ...

def board_usage_json(usage: BoardUsage) -> _Program[dict, object]:
    ...

def board_answer_json(answer: BoardRead | BoardWritten | BoardConflict | BoardRefused) -> _Program[dict, object]:
    ...

def task_answer_json(answer: TaskAccepted | TaskProgress | TaskMissing | TaskResultTaken | TaskDropped) -> _Program[dict, object]:
    ...

def detached_answer_json(answer: DetachedSubmitted | DetachedProgress | DetachedUnknown | DetachedWarming | DetachedCancelled | DetachedReleased) -> _Program[dict, object]:
    ...

def reply_json(body: EventsView | StateReply | HeartbeatReply | WorkerDrainView | ErrorReply | BoardRead | BoardWritten | BoardConflict | BoardRefused | LeaseAnswer | WarmState | ProgramStored | ProgramRow | TaskAccepted | TaskProgress | TaskMissing | TaskResultTaken | TaskDropped | DetachedSubmitted | DetachedProgress | DetachedUnknown | DetachedWarming | DetachedCancelled | DetachedReleased | DetachedOutbox | DetachedOutboxAcked | ResourceView | ResourceList | PlainText | dict | list | tuple | str | int | float | bool | None) -> _Program[PlainText | dict | list | tuple | str | int | float | bool | None, object]:
    ...
reply_bodies: _Handler
