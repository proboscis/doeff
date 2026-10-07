# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = handler_sets.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff_cluster.coordinator.protocol.request_bodies import request_bodies as request_bodies
from doeff_core_effects.handlers import await_handler as await_handler
from doeff_core_effects.handlers import slog_handler as slog_handler
from doeff_time import async_time_handler as async_time_handler
from doeff_cluster.coordinator.protocol.request_queue import RequestQueue as RequestQueue
from doeff_cluster.coordinator.protocol.request_queue import queued_requests as queued_requests
from doeff_cluster.foundation.wal_store import WalStore as WalStore
from doeff_cluster.coordinator.protocol.store import durable_states as durable_states
from doeff_cluster.coordinator.protocol.store import wal_store as wal_store
from doeff_cluster.coordinator.protocol.replies import reply_bodies as reply_bodies
from doeff_cluster.coordinator.protocol.kube import KubeMemory as KubeMemory
from doeff_cluster.coordinator.protocol.kube import MemoryFollows as MemoryFollows
from doeff_cluster.coordinator.protocol.kube import kube_memory as kube_memory
from doeff_cluster.foundation.coordinator_inbox import RequestInbox as RequestInbox
from doeff_cluster.foundation.coordinator_inbox import StopState as StopState
from doeff_cluster.shared.protocol.inbox import http_requests as http_requests
from doeff_cluster.shared.protocol.inbox import stop_flag as stop_flag
from doeff_cluster.coordinator.protocol.faults import coordinator_faults as coordinator_faults
from doeff_events import MemoryBroker as MemoryBroker
from doeff_events import broker_back_by_retry as broker_back_by_retry
from doeff_events import memory_notice_handler as memory_notice_handler
from doeff_events import notice_events_handler as notice_events_handler
from doeff_events import redis_notice_handler as redis_notice_handler
from doeff_cluster.coordinator.protocol.worker_notices import WORKER_NOTICE_ROUTES as WORKER_NOTICE_ROUTES
NOTICE_SOURCE: str
NOTICE_PATIENCE_SECONDS: float

def redis_notices(url: str, timeout_seconds: float, retry_seconds: float) -> list:
    ...

def memory_notices(broker: MemoryBroker) -> list:
    ...

def production_handlers(inbox: RequestInbox, store: WalStore, stop: StopState, kube: object, notices: list) -> list:
    ...

class MemoryWalStore:
    kv: Incomplete
    seq: Incomplete
    deltas: Incomplete
    fail_at: Incomplete
    recovered: Incomplete

    def __init__(self) -> None:
        ...

    def exists(self) -> bool:
        ...

    def load(self) -> dict:
        ...

    def table(self) -> dict[str, object]:
        ...

    def replace_table(self, kv: dict[str, object]) -> None:
        ...

    def recovery(self) -> dict[str, int | str] | None:
        ...

    def persist(self, delta: dict[str, object]) -> None:
        ...

    def checkpoint(self) -> None:
        ...

def emulated_handlers(queue: RequestQueue, store: MemoryWalStore, stop: StopState, kube: KubeMemory, broker: MemoryBroker, watchers: list=...) -> list:
    ...
