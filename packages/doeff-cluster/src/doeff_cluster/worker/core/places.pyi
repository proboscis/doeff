# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = places.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from doeff_cluster.worker.core.warm_rules import warm_dir_of as warm_dir_of

@dataclass(frozen=True, kw_only=True)
class WorkerPlaces:
    code: str
    jobs: str
    programs: str
    tasks: str
    warm: str
    probe: str
    status: str
    logs: str

def worker_places(state: str, shared: str) -> _Program[WorkerPlaces, object]:
    ...
