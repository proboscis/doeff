# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = meter_effects.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass
from doeff_hy.frozen import FrozenMap as FrozenMap
import doeff_hy.record

@dataclass(frozen=True, kw_only=True)
class SecondsTotal:
    total: float
    count: int

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class MeterSnapshot:
    counters: FrozenMap[float]
    gauges: FrozenMap[float]
    durations: FrozenMap[SecondsTotal]
EMPTY_METER: MeterSnapshot

@dataclass(frozen=True, kw_only=True)
class MeterBucket:
    label: str
    ceiling: float

@dataclass(frozen=True, kw_only=True)
class MeterSettings:
    buckets: tuple[MeterBucket, ...] = ...
    inf_label: str = 'le_inf'
    gc_pause_name: str | None = None

@_doeff_dataclass(frozen=True)
class CountMetric(_doeff_effect_base[None]):
    name: str
    amount: float = 1.0

@_doeff_dataclass(frozen=True)
class ObserveSeconds(_doeff_effect_base[None]):
    name: str
    seconds: float

@_doeff_dataclass(frozen=True)
class SetGauge(_doeff_effect_base[None]):
    name: str
    value: float

@_doeff_dataclass(frozen=True)
class ReadMeter(_doeff_effect_base[MeterSnapshot]):
    ...

def counted(snapshot: MeterSnapshot, name: str, amount: float) -> _Program[MeterSnapshot, object]:
    ...

def gauged(snapshot: MeterSnapshot, name: str, value: float) -> _Program[MeterSnapshot, object]:
    ...

def observed(snapshot: MeterSnapshot, settings: MeterSettings, name: str, seconds: float) -> _Program[MeterSnapshot, object]:
    ...
