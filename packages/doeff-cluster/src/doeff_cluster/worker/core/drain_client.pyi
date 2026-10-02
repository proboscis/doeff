# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = drain_client.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_time import Delay as Delay
from doeff_time import GetMonotonic as GetMonotonic
from doeff_cluster.worker.intent.drain_model import CoordinatorCall as CoordinatorCall
from doeff_cluster.worker.intent.drain_model import AskDrain as AskDrain
DRAIN_DEADLINE_SECONDS: float
DRAIN_INTERVAL_SECONDS: float
DRAIN_TTL_MARGIN_SECONDS: float

def worker_path(name: str) -> str:
    ...

def drain_outcome(answer: dict, elapsed: float, deadline: float) -> str | None:
    ...

def await_drained(name: str, deadline: float, interval: float, own_boot: str | None=None) -> _Program[dict, object]:
    ...

def ready_of(answer: dict, own_boot: str | None) -> bool:
    ...

def worker_ready(name: str, own_boot: str | None) -> _Program[bool, object]:
    ...
