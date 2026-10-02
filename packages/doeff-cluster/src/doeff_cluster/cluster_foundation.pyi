# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = cluster_foundation.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from dataclasses import replace as replace
from doeff import Program as Program
from doeff import EffectBase as EffectBase
from doeff import with_handlers as with_handlers
from doeff_core_effects.effects import Ask as Ask
from doeff_cluster.foundation.host_contract import HOST_CONTRACT as HOST_CONTRACT
from .job_context import RunContext as RunContext
from .job_context import runtime_env_of_context as runtime_env_of_context
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv as RuntimeEnv
from doeff_cluster.shared.protocol.service_report import ServiceReport as ServiceReport
from doeff_cluster.shared.protocol.service_report import service_report_of as service_report_of
from doeff_cluster.shared.protocol.readiness_handlers import readiness_http as readiness_http
from doeff_cluster.shared.protocol.metrics_handlers import metrics_http as metrics_http
from doeff_cluster.shared.protocol.shared_handlers import shared_http as shared_http
from doeff_cluster.shared.core.clock import now_epoch_ms as now_epoch_ms
from doeff_cluster.shared.protocol.coordinator_route import CoordinatorRoute as CoordinatorRoute
from doeff_cluster.shared.protocol.coordinator_route import RouteCell as RouteCell
from doeff_cluster.shared.protocol.coordinator_route import RouteOptions as RouteOptions
from doeff_cluster.shared.protocol.coordinator_route import route_of as route_of
from doeff_cluster.foundation.coordinator_http import REPLY_SECONDS as REPLY_SECONDS
from doeff_cluster.foundation.coordinator_http import CONNECT_SECONDS as CONNECT_SECONDS
from doeff_cluster.foundation.coordinator_http import PREFERRED_RECHECK_SECONDS as PREFERRED_RECHECK_SECONDS
from doeff_cluster.foundation.coordinator_http import RESEND_PAUSE_SECONDS as RESEND_PAUSE_SECONDS
from doeff_cluster.foundation.coordinator_http import default_actor as default_actor
from doeff_cluster.shared.core.resend import IDEMPOTENT_DEADLINE_SECONDS as IDEMPOTENT_DEADLINE_SECONDS
from doeff_cluster.shared.core.semaphore_handlers import cluster_semaphore as cluster_semaphore
from doeff_cluster.shared.core.semaphore_handlers import SemaphoreSession as SemaphoreSession
from doeff_cluster.shared.core.lease_rules import lease_holder as lease_holder
from doeff_cluster.shared.protocol.remote import remote_cluster as remote_cluster
from doeff_cluster.shared.protocol.remote import TaskSender as TaskSender
from doeff_cluster.shared.protocol.detached import detached_cluster as detached_cluster
from doeff_cluster.shared.protocol.detached import DetachedSender as DetachedSender
from doeff_cluster.shared.protocol.detached import warm_cluster as warm_cluster

def lease_holder_of(ctx: RunContext) -> _Program[str, object]:
    ...

def coordinator_route_options() -> _Program[RouteOptions, object]:
    ...

def cluster_handlers() -> _Program[list, object]:
    ...

def with_cluster_handlers(body: Program | EffectBase) -> _Program[Incomplete, object]:
    ...
