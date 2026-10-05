# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = memory_meter.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff_hy.static_types import Handler as _Handler
from doeff_core_effects.meter_effects import CountMetric as CountMetric
from doeff_core_effects.meter_effects import EMPTY_METER as EMPTY_METER
from doeff_core_effects.meter_effects import MeterSettings as MeterSettings
from doeff_core_effects.meter_effects import MeterSnapshot as MeterSnapshot
from doeff_core_effects.meter_effects import ObserveSeconds as ObserveSeconds
from doeff_core_effects.meter_effects import ObserveSecondsBatch as ObserveSecondsBatch
from doeff_core_effects.meter_effects import ReadMeter as ReadMeter
from doeff_core_effects.meter_effects import SetGauge as SetGauge
from doeff_core_effects.meter_effects import counted as counted
from doeff_core_effects.meter_effects import gauged as gauged
from doeff_core_effects.meter_effects import observed as observed
from doeff_core_effects.meter_effects import observed_batch as observed_batch
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put

def memory_meter_handler(settings: MeterSettings) -> _Handler:
    ...
