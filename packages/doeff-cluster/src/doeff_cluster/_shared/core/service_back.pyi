# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = service_back.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from doeff_core_effects import Get as Get
from doeff_core_effects import Put as Put
from doeff_core_effects import Cancel as Cancel
from doeff_core_effects import CompletePromise as CompletePromise
from doeff_core_effects import CreatePromise as CreatePromise
from doeff_core_effects import Promise as Promise
from doeff_core_effects import Spawn as Spawn
from doeff_core_effects import Task as Task
from doeff_core_effects import TaskCancelledError as TaskCancelledError
from doeff_core_effects import Wait as Wait
from doeff_core_effects import shielded as shielded
from doeff_cluster._shared.core.promise_wait import promise_or_timeout as promise_or_timeout
from doeff_cluster._shared.intent.detached_model import AwaitRunnersChange as AwaitRunnersChange
from doeff_cluster._shared.intent.detached_model import AwaitServiceReady as AwaitServiceReady
from doeff_cluster._shared.intent.detached_model import RunnersChange as RunnersChange
from doeff_cluster._shared.intent.detached_model import RunnersUnreachable as RunnersUnreachable
from doeff_cluster._shared.intent.detached_model import RunnersWatchMissing as RunnersWatchMissing
from doeff_cluster._shared.intent.detached_model import ServiceReady as ServiceReady
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
