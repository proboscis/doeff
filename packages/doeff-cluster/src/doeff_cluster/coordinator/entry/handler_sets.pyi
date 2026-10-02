# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = handler_sets.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

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
from doeff_cluster.coordinator.protocol.kube import kube_memory as kube_memory
from doeff_cluster.foundation.coordinator_inbox import RequestInbox as RequestInbox
from doeff_cluster.foundation.coordinator_inbox import StopState as StopState
from doeff_cluster.shared.protocol.inbox import http_requests as http_requests
from doeff_cluster.shared.protocol.inbox import stop_flag as stop_flag
from doeff_cluster.coordinator.protocol.faults import coordinator_faults as coordinator_faults

def production_handlers(inbox: RequestInbox, store: WalStore, stop: StopState, kube: object) -> list:
    ...

class MemoryWalStore:

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

def emulated_handlers(queue: RequestQueue, store: MemoryWalStore, stop: StopState, kube: KubeMemory, watchers: list=...) -> list:
    ...
