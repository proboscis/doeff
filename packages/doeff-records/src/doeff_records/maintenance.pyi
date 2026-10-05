# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = maintenance.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from doeff import EffectBase as EffectBase
from doeff_time import Delay as Delay
from doeff_records.values import Unreachable as Unreachable

@dataclass(frozen=True)
class SweepExpired(EffectBase):
    ...

@dataclass(frozen=True)
class PruneChanges(EffectBase):
    keep_seconds: float

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class Swept:
    rows: int

@dataclass(frozen=True)
class Pruned:
    floor: int
    removed: int

@dataclass(frozen=True)
class MaintenanceReport:
    swept: Swept | Unreachable
    pruned: Pruned | Unreachable

def maintenance_tick(keep_seconds: int | float) -> _Program[MaintenanceReport, object]:
    ...

def maintenance_loop(interval_seconds: int | float, keep_seconds: int | float, ticks: int | None) -> _Program[list, object]:
    ...
