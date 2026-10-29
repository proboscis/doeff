# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _memory_meter.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from doeff_core_effects._meter_effects import CountMetric as CountMetric
from doeff_core_effects._meter_effects import EMPTY_METER as EMPTY_METER
from doeff_core_effects._meter_effects import MeterSettings as MeterSettings
from doeff_core_effects._meter_effects import MeterSnapshot as MeterSnapshot
from doeff_core_effects._meter_effects import ObserveSeconds as ObserveSeconds
from doeff_core_effects._meter_effects import ObserveSecondsBatch as ObserveSecondsBatch
from doeff_core_effects._meter_effects import ReadMeter as ReadMeter
from doeff_core_effects._meter_effects import SetGauge as SetGauge
from doeff_core_effects._meter_effects import gauged as gauged
from doeff_core_effects._meter_effects import metered as metered
METERED_WRITES: int

@dataclass(frozen=True, kw_only=True)
class MeterHold:
    snapshot: MeterSnapshot
    pending: tuple
EMPTY_HOLD: MeterHold

def held_write(hold: MeterHold, write: CountMetric | ObserveSeconds | ObserveSecondsBatch) -> _Program[MeterHold, object]:
    ...

def folded(hold: MeterHold, settings: MeterSettings) -> _Program[MeterHold, object]:
    ...

def memory_meter_handler(settings: MeterSettings) -> _Handler:
    ...
