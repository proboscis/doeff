# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = failover_model.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass
from doeff_cluster._coordinator.intent.cluster_model import WorkerInfo as WorkerInfo
from doeff_cluster.shared.intent.service_model import UPDATE_FORMS as UPDATE_FORMS

@dataclass(frozen=True, kw_only=True)
class FailoverWorker:
    info: WorkerInfo
    memory_mib: int | None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class ResidentJob:
    name: str
    needs: tuple[str, ...]
    replicas: int
    update: str
    peak_mib: int
    settle_mib: int
    single_host: str | None = None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class ExtraProvides:
    name: str
    provides: tuple[str, ...]

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class KeptApart:
    first: str
    second: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class JobTakers:
    job: ResidentJob
    takers: tuple[str, ...]
    per_machine: bool

@dataclass(frozen=True, kw_only=True)
class DownSpike:
    down: str
    moved: tuple[ResidentJob, ...]
    extra_mib: int

    def __post_init__(self) -> None:
        ...
