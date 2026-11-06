# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = launch_model.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass

@dataclass(frozen=True)
class WorkerLaunch:
    name: str
    provides: tuple[str, ...]
    exclusive: tuple[str, ...]
    doeff_commit: str
    memory_mib: int | None = None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class StaticWorkerLaunch:
    name: str
    doeff_commit: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class CoordinatorLaunch:
    doeff_commit: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class LaunchLineChange:
    unit: str
    name: str
    before: str
    after: str

@_doeff_dataclass(frozen=True)
class DesireWorker(_doeff_effect_base[tuple[LaunchLineChange, ...]]):
    launch: WorkerLaunch | StaticWorkerLaunch

@_doeff_dataclass(frozen=True)
class DesireCoordinator(_doeff_effect_base[tuple[LaunchLineChange, ...]]):
    launch: CoordinatorLaunch
