# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = semaphore_handlers.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from collections.abc import Callable as Callable
from doeff import EffectBase as EffectBase
from doeff_core_effects.scheduler import CreateSemaphore as CreateSemaphore
from doeff_core_effects.scheduler import AcquireSemaphore as AcquireSemaphore
from doeff_core_effects.scheduler import ReleaseSemaphore as ReleaseSemaphore
from doeff_core_effects.scheduler import Spawn as Spawn
from doeff_core_effects.scheduler import Cancel as Cancel
from doeff_time import Delay as Delay
from doeff_cluster.shared.core.clock import now_epoch_ms as now_epoch_ms
from doeff_cluster.shared.intent.semaphore_model import CreateNamedSemaphore as CreateNamedSemaphore
from doeff_cluster.shared.intent.semaphore_model import ClusterSemaphore as ClusterSemaphore
from doeff_cluster.shared.intent.semaphore_model import LeaseLost as LeaseLost
from doeff_cluster.shared.intent.semaphore_model import HeldLease as HeldLease
from doeff_cluster.shared.intent.semaphore_model import WriteFenced as WriteFenced
from doeff_cluster.shared.intent.semaphore_model import LeaseStanding as LeaseStanding
from doeff_cluster.shared.intent.semaphore_model import LeaseOp as LeaseOp
from doeff_cluster.shared.intent.semaphore_model import LeaseAnswer as LeaseAnswer
from doeff_cluster.shared.intent.semaphore_model import AwaitLeaseFree as AwaitLeaseFree
from doeff_cluster.shared.intent.semaphore_model import STANDBY as STANDBY
from doeff_cluster.shared.intent.semaphore_model import HELD as HELD
from doeff_cluster.shared.intent.semaphore_model import LOST as LOST
from doeff_cluster.shared.core.lease_rules import fence_verdict as fence_verdict
from doeff_cluster.shared.core.lease_rules import holder_tokens_prefix as holder_tokens_prefix
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler

def named_semaphore_local(names: dict) -> _Handler:
    ...

class SemaphoreSession:
    holder: str
    ttl_seconds: float
    retry_seconds: float
    seq: Incomplete
    held: Incomplete
    lost: Incomplete
    renewers: Incomplete
    expires: Incomplete
    ever_held: Incomplete

    def __init__(self, holder: str, ttl_seconds: float=15.0, retry_seconds: float=0.5) -> None:
        ...

    def next_token(self) -> str:
        ...

    def ttl_ms(self) -> int:
        ...

    def hold(self, name: str, token: str) -> None:
        ...

    def standing_of(self, name: str) -> str:
        ...

    def holds(self, token: str) -> bool:
        ...

    def take(self, name: str) -> str | None:
        ...

    def hold_of(self, name: str) -> dict | None:
        ...

def acquire_lease(session: SemaphoreSession, semaphore: ClusterSemaphore) -> _Program[str, object]:
    ...

def renew_lease(session: SemaphoreSession, semaphore: ClusterSemaphore, token: str) -> _Program[None, object]:
    ...

def release_lease(session: SemaphoreSession, semaphore: ClusterSemaphore) -> _Program[None, object]:
    ...

def cluster_semaphore(session: SemaphoreSession) -> _Handler:
    ...

def leases_fence(leases_of: Callable, write_types: tuple, margin_ms: int) -> _Handler:
    ...

def standby_divert(name: str, write_types: tuple) -> _Handler:
    ...
