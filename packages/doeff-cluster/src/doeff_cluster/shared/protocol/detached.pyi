# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = detached.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from urllib.parse import quote as url_quote
from operator import itemgetter as itemgetter
from doeff_time import Delay as Delay
from doeff_core_effects.http_effects import HttpResponse as HttpResponse
from doeff_core_effects.http_effects import HttpFailed as HttpFailed
from doeff_cluster.shared.protocol.coordinator_route import RouteCell as RouteCell
from doeff_cluster.shared.protocol.coordinator_route import RouteOptions as RouteOptions
from doeff_cluster.shared.protocol.coordinator_route import RoutedReply as RoutedReply
from doeff_cluster.shared.protocol.coordinator_route import routed_request as routed_request
from doeff_cluster.shared.protocol.coordinator_route import resent_request as resent_request
from doeff_cluster.shared.protocol.coordinator_route import answer_json as answer_json
from doeff_cluster.shared.core.remote_rules import program_sha as program_sha
from doeff_cluster.shared.intent.protocol import PROTOCOL_FORMAT as PROTOCOL_FORMAT
from doeff_cluster.shared.intent.protocol import WATCH_MAX_SECONDS as WATCH_MAX_SECONDS
from doeff_cluster.shared.core.capabilities import env_mapping as env_mapping
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv as RuntimeEnv
from doeff_cluster.shared.core.runtime_env_rules import hyx_runtime_env_XgreaterHthan_signXjson as hyx_runtime_env_XgreaterHthan_signXjson
from doeff_cluster.shared.intent.warm_model import WarmRuntimeEnv as WarmRuntimeEnv
from doeff_cluster.shared.intent.warm_model import ReadWarmState as ReadWarmState
from doeff_cluster.shared.intent.warm_model import WarmState as WarmState
from doeff_cluster.shared.intent.warm_model import WarmUnreachable as WarmUnreachable
from doeff_cluster.shared.intent.warm_model import WarmAnswer as WarmAnswer
from doeff_cluster.shared.intent.warm_model import AwaitWarm as AwaitWarm
from doeff_cluster.shared.intent.warm_model import WarmReady as WarmReady
from doeff_cluster.shared.intent.warm_model import WarmFailed as WarmFailed
from doeff_cluster.shared.intent.warm_model import WarmWaitExpired as WarmWaitExpired
from doeff_cluster.shared.core.warm_rules import warm_state_of_json as warm_state_of_json
from doeff_cluster.shared.core.warm_rules import warm_wait_answer as warm_wait_answer
from doeff_cluster.shared.core.clock import now_epoch_ms as now_epoch_ms
from doeff_cluster.shared.intent.process_model import AwaitProcessEnded as AwaitProcessEnded
from doeff_cluster.shared.intent.process_model import ProcessEnded as ProcessEnded
from doeff_cluster.shared.intent.process_model import ProcessWaitExpired as ProcessWaitExpired
from doeff_cluster.shared.intent.detached_model import SubmitDetached as SubmitDetached
from doeff_cluster.shared.intent.detached_model import AwaitDetached as AwaitDetached
from doeff_cluster.shared.intent.detached_model import CancelDetached as CancelDetached
from doeff_cluster.shared.intent.detached_model import ReleaseDetached as ReleaseDetached
from doeff_cluster.shared.intent.detached_model import ReadRunners as ReadRunners
from doeff_cluster.shared.intent.detached_model import WARMING_PHASE as WARMING_PHASE
from doeff_cluster.shared.intent.detached_model import DetachedSubmitted as DetachedSubmitted
from doeff_cluster.shared.intent.detached_model import DetachedPending as DetachedPending
from doeff_cluster.shared.intent.detached_model import DetachedRefused as DetachedRefused
from doeff_cluster.shared.intent.detached_model import DetachedAwaited as DetachedAwaited
from doeff_cluster.shared.intent.detached_model import DetachedUnreachable as DetachedUnreachable
from doeff_cluster.shared.intent.detached_model import DetachedSubmitAnswer as DetachedSubmitAnswer
from doeff_cluster.shared.intent.detached_model import RunnerFact as RunnerFact
from doeff_cluster.shared.intent.detached_model import RunnersUnreachable as RunnersUnreachable
from doeff_cluster.shared.intent.detached_model import OPEN_PHASES as OPEN_PHASES
from doeff_cluster.shared.intent.detached_model import DetachedOutcome as DetachedOutcome
from doeff_cluster.shared.intent.detached_model import DetachedSucceeded as DetachedSucceeded
from doeff_cluster.shared.intent.detached_model import DetachedFailed as DetachedFailed
from doeff_cluster.shared.intent.detached_model import DetachedLost as DetachedLost
from doeff_cluster.shared.intent.detached_model import DetachedCancelled as DetachedCancelled
from doeff_cluster.shared.intent.detached_model import DetachedVersionMismatch as DetachedVersionMismatch
from doeff_cluster.shared.intent.detached_model import DetachedUnrunnable as DetachedUnrunnable
from doeff_cluster.shared.intent.detached_model import DetachedEnvUnavailable as DetachedEnvUnavailable
from doeff_cluster.shared.intent.detached_model import DetachedUnknown as DetachedUnknown
from doeff_cluster.shared.intent.detached_model import AwaitRunnersChange as AwaitRunnersChange
from doeff_cluster.shared.intent.detached_model import RunnersChange as RunnersChange
from doeff_cluster.shared.intent.detached_model import RunnersWatchMissing as RunnersWatchMissing
from doeff_cluster.shared.intent.detached_model import RunnersChangeAnswer as RunnersChangeAnswer
from doeff_cluster.shared.intent.detached_model import AwaitServiceReady as AwaitServiceReady
from doeff_cluster.shared.intent.detached_model import ServiceReady as ServiceReady
from doeff_cluster.shared.intent.detached_model import ServiceViewWire as ServiceViewWire
from doeff_cluster.shared.intent.detached_model import ReadServices as ReadServices
from doeff_cluster.shared.intent.detached_model import ServiceFact as ServiceFact
from doeff_cluster.shared.intent.detached_model import ServicesUnreachable as ServicesUnreachable
from doeff_hy.wire import Malformed as Malformed
from doeff_hy.wire import parse as parse
from doeff_cluster.shared.intent.remote_model import TaskSucceeded as TaskSucceeded
from doeff_cluster.shared.intent.remote_model import TaskFailed as TaskFailed
from doeff_cluster.shared.protocol.program_codec import encode_program as encode_program
from doeff_cluster.shared.protocol.program_codec import decode_outcome as decode_outcome
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler

def outcome_from_task_outcome(outcome: TaskSucceeded | TaskFailed) -> DetachedOutcome:
    ...

def decoded_result(blob: str) -> DetachedOutcome:
    ...

def outcome_of_view(view: dict) -> DetachedOutcome | None:
    ...
REFUSED_STATUSES: tuple[int, ...]

def detached_path(key: str, suffix: str) -> str:
    ...

def detached_submit_body(blob: str, versions: dict, revision: str, needs: frozenset, name: str, lease_seconds: float, retain_seconds: float, runtime_env: dict | None, environ: dict) -> _Program[dict, object]:
    ...

def detached_refusal(status: int | None, body: dict | None) -> DetachedRefused | None:
    ...

def submit_unreachable(reason: str) -> DetachedUnreachable:
    ...

def awaited_answer(view: dict | None, reason: str, key: str, waited: float, timeout_seconds: float | int | None) -> DetachedAwaited | None:
    ...

def runner_facts_of_view(workers: dict) -> tuple:
    ...

def runners_change_of(status: int | None, body: dict | list | str | int | float | bool | None) -> RunnersChangeAnswer:
    ...

def watch_query(after: int, timeout_seconds: float) -> dict:
    ...

def service_ready_of(status: int | None, body: dict | list | str | int | float | bool | None) -> _Program[bool | None, object]:
    ...

def service_readiness(cell: RouteCell, options: RouteOptions, sender: DetachedSender, name: str) -> _Program[bool | None, object]:
    ...

def service_ready_awaited(cell: RouteCell, options: RouteOptions, sender: DetachedSender, name: str, poll_seconds: float) -> _Program[ServiceReady, object]:
    ...

def runners_unreachable(reason: str) -> RunnersUnreachable:
    ...

def service_facts_of_view(items: list) -> tuple:
    ...

def services_unreachable(reason: str) -> ServicesUnreachable:
    ...

def warm_request_body(runtime_env: dict, needs: frozenset, ttl_seconds: float, holder: str) -> dict:
    ...

def warm_path(key: str) -> str:
    ...

def absent_warm_state(key: str) -> WarmState:
    ...
SERVER_ERROR: int

def warm_unconnected(reason: str) -> WarmUnreachable:
    ...

def warm_server_failure(status: int, text: str) -> WarmUnreachable:
    ...

@dataclass(frozen=True, kw_only=True)
class DetachedSender:
    revision: str
    versions: dict
    runtime_env: RuntimeEnv | None
    deadline_seconds: float

def resent_answer(cell: RouteCell, options: RouteOptions, method: str, path: str, params: dict | None, body: dict | None, deadline_seconds: float) -> _Program[HttpResponse | HttpFailed | None, object]:
    ...

def detached_json(answer: HttpResponse | HttpFailed | None) -> _Program[dict | list | str | int | float | bool | None, object]:
    ...

def detached_submitted(cell: RouteCell, options: RouteOptions, sender: DetachedSender, key: str, blob: str, needs: frozenset, name: str, lease_seconds: float, retain_seconds: float, environ: dict) -> _Program[DetachedSubmitAnswer, object]:
    ...

def detached_view(cell: RouteCell, options: RouteOptions, sender: DetachedSender, key: str) -> _Program[dict | str, object]:
    ...

def detached_flag(cell: RouteCell, options: RouteOptions, sender: DetachedSender, method: str, suffix: str, key: str, field: str) -> _Program[bool, object]:
    ...

def runners_read(cell: RouteCell, options: RouteOptions, sender: DetachedSender) -> _Program[tuple | RunnersUnreachable, object]:
    ...

def services_read(cell: RouteCell, options: RouteOptions, sender: DetachedSender) -> _Program[tuple | ServicesUnreachable, object]:
    ...

def runners_changed(cell: RouteCell, options: RouteOptions, after: int, timeout_seconds: float) -> _Program[RunnersChangeAnswer, object]:
    ...

def await_cluster(cell: RouteCell, options: RouteOptions, sender: DetachedSender, key: str, timeout_seconds: float | int | None, poll_seconds: float) -> _Program[DetachedAwaited, object]:
    ...
LIVE_JOB_PHASES: frozenset[str]
UNSTARTED_JOB_PHASES: frozenset[str]

@dataclass(frozen=True, kw_only=True)
class ProcessWatch:
    watched: ProcessEnded | None
    ended: ProcessEnded | None

def process_watch_step(statuses: dict, job: str, watched: ProcessEnded | None) -> _Program[ProcessWatch, object]:
    ...

def await_process_cluster(cell: RouteCell, options: RouteOptions, sender: DetachedSender, job: str, timeout_seconds: float | int | None, poll_seconds: float) -> _Program[ProcessEnded | ProcessWaitExpired, object]:
    ...

def detached_cluster(cell: RouteCell, options: RouteOptions, sender: DetachedSender, poll_seconds: float=1.0) -> _Handler:
    ...

def warm_answer(answer: HttpResponse | HttpFailed | None, key: str | None) -> _Program[WarmAnswer, object]:
    ...

def warm_written(cell: RouteCell, options: RouteOptions, deadline_seconds: float, env: RuntimeEnv, needs: frozenset, ttl_seconds: float, holder: str) -> _Program[WarmAnswer, object]:
    ...

def warm_read(cell: RouteCell, options: RouteOptions, deadline_seconds: float, key: str) -> _Program[WarmAnswer, object]:
    ...
WARM_UNREACHED_PAUSE_SECONDS: float

def warm_awaited(cell: RouteCell, options: RouteOptions, key: str, timeout_seconds: float, poll_seconds: float) -> _Program[WarmReady | WarmFailed | WarmWaitExpired, object]:
    ...

def warm_cluster(cell: RouteCell, options: RouteOptions) -> _Handler:
    ...
