# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = launch_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass

@dataclass(frozen=True)
class WorkerLaunch:
    name: str
    provides: tuple[str, ...]
    exclusive: tuple[str, ...]
    capacity: int
    task_reserve: int
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
    launch: WorkerLaunch

@_doeff_dataclass(frozen=True)
class DesireCoordinator(_doeff_effect_base[tuple[LaunchLineChange, ...]]):
    launch: CoordinatorLaunch
