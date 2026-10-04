# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = service_back.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_core_effects.effects import Get as Get
from doeff_core_effects.effects import Put as Put
from doeff_core_effects.scheduler import Cancel as Cancel
from doeff_core_effects.scheduler import CompletePromise as CompletePromise
from doeff_core_effects.scheduler import CreatePromise as CreatePromise
from doeff_core_effects.scheduler import Promise as Promise
from doeff_core_effects.scheduler import Spawn as Spawn
from doeff_core_effects.scheduler import Task as Task
from doeff_core_effects.scheduler import TaskCancelledError as TaskCancelledError
from doeff_core_effects.scheduler import Wait as Wait
from doeff_cluster.shared.core.promise_wait import promise_or_timeout as promise_or_timeout
from doeff_cluster.shared.intent.detached_model import AwaitRunnersChange as AwaitRunnersChange
from doeff_cluster.shared.intent.detached_model import AwaitServiceReady as AwaitServiceReady
from doeff_cluster.shared.intent.detached_model import RunnersChange as RunnersChange
from doeff_cluster.shared.intent.detached_model import RunnersUnreachable as RunnersUnreachable
from doeff_cluster.shared.intent.detached_model import RunnersWatchMissing as RunnersWatchMissing
from doeff_cluster.shared.intent.detached_model import ServiceReady as ServiceReady
SERVICE_BACK_CHANGE_SECONDS: float
SERVICE_BACK_STATE: str

def service_back(name: str) -> _Program[ServiceReady, object]:
    ...

def back_announced(name: str, promise: Promise) -> _Program[None, object]:
    ...

def stopped(task: Task) -> _Program[None, object]:
    ...

def service_back_within(name: str, seconds: int | float) -> _Program[ServiceReady | None, object]:
    ...
