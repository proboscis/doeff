# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = places.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from doeff_cluster._worker.core.warm_rules import warm_dir_of as warm_dir_of
GENERATION_MARK_LENGTH: int
HEX_DIGITS: str

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
    generation: str

def worker_places(state: str, shared: str, boot: str) -> _Program[WorkerPlaces, object]:
    ...
