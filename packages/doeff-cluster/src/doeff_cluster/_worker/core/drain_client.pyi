# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = drain_client.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from doeff_time import Delay as Delay
from doeff_time import GetMonotonic as GetMonotonic
from doeff_cluster._worker.intent.drain_model import CoordinatorCall as CoordinatorCall
from doeff_cluster._worker.intent.drain_model import AskDrain as AskDrain
DRAIN_DEADLINE_SECONDS: float
DRAIN_INTERVAL_SECONDS: float
DRAIN_TTL_MARGIN_SECONDS: float

def worker_path(name: str) -> str:
    ...

def drain_outcome(answer: dict[str, object], elapsed: float, deadline: float) -> str | None:
    ...

def await_drained(name: str, deadline: float, interval: float, own_boot: str | None=None) -> _Program[dict[str, object], object]:
    ...

def ready_of(answer: dict[str, object], own_boot: str | None) -> bool:
    ...

def worker_ready(name: str, own_boot: str | None) -> _Program[bool, object]:
    ...
