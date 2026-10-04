# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = deployed_cluster.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from doeff import with_handlers as with_handlers
from doeff import Program as Program
from doeff import EffectBase as EffectBase
from doeff_core_effects.handlers import await_handler as await_handler
from doeff_core_effects.handlers import slog_handler as slog_handler
from doeff_core_effects.scheduler import scheduled as scheduled
from doeff_core_effects.http_handlers import http_production_handler as http_production_handler
from doeff_time import async_time_handler as async_time_handler
from doeff_cluster.foundation.process_versions import this_process_versions as this_process_versions
from doeff_cluster.shared.entry.declare import apply_declaration as apply_declaration
from doeff_cluster.shared.entry.service_build import system_declaration as system_declaration
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv as RuntimeEnv
from doeff_cluster.shared.intent.cluster_control import ServiceReadiness as ServiceReadiness
from doeff_cluster.shared.intent.cluster_control import ReadinessOf as ReadinessOf
from doeff_cluster.shared.intent.cluster_control import ReadinessWaitExpired as ReadinessWaitExpired
from doeff_cluster.shared.intent.cluster_control import AwaitReadiness as AwaitReadiness
from doeff_cluster.shared.intent.cluster_control import AwaitJobProcess as AwaitJobProcess
from doeff_cluster.shared.intent.cluster_control import JobProcessSeen as JobProcessSeen
from doeff_cluster.shared.intent.cluster_control import JobProcessWaitExpired as JobProcessWaitExpired
from doeff_cluster.shared.intent.cluster_control import Redeclare as Redeclare
from doeff_cluster.shared.intent.cluster_control import Crash as Crash
from doeff_cluster.shared.intent.cluster_control import KillWorker as KillWorker
from doeff_cluster.shared.intent.cluster_control import StopWorker as StopWorker
from doeff_cluster.shared.intent.cluster_control import StopCoordinator as StopCoordinator
from doeff_cluster.shared.intent.cluster_control import CrashCoordinator as CrashCoordinator
from doeff_cluster.shared.protocol.coordinator_reads import readiness_read as readiness_read
from doeff_cluster.shared.protocol.coordinator_reads import readiness_awaited as readiness_awaited
from doeff_cluster.shared.protocol.coordinator_reads import job_process_awaited as job_process_awaited
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler

class DeployedCannotAnswer(Exception):
    ...

@dataclass(frozen=True, kw_only=True)
class DeployedCluster:
    url: str
    actor: str
    revision: str
    runtime_env: RuntimeEnv | None

def deployed_cluster_answers(target: DeployedCluster) -> _Handler:
    ...

def deployed_cluster(scenario: Program | EffectBase, *, target: DeployedCluster) -> _Program[Incomplete, object]:
    ...
