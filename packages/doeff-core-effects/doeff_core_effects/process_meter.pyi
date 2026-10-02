# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = process_meter.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from collections import deque as deque
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from doeff_core_effects.meter_effects import CountMetric as CountMetric
from doeff_core_effects.meter_effects import EMPTY_METER as EMPTY_METER
from doeff_core_effects.meter_effects import MeterSettings as MeterSettings
from doeff_core_effects.meter_effects import MeterSnapshot as MeterSnapshot
from doeff_core_effects.meter_effects import ObserveSeconds as ObserveSeconds
from doeff_core_effects.meter_effects import ReadMeter as ReadMeter
from doeff_core_effects.meter_effects import SetGauge as SetGauge
from doeff_core_effects.meter_effects import counted as counted
from doeff_core_effects.meter_effects import gauged as gauged
from doeff_core_effects.meter_effects import observed as observed
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put

@dataclass(frozen=True, kw_only=True)
class MeterPlace:
    settings: MeterSettings
    lock: object
    pauses: deque
PLACES: Incomplete
SNAPSHOTS: Incomplete
PLACES_LOCK: Incomplete

def gc_pause_watch(pauses: deque) -> _Program[Callable, object]:
    ...

def meter_place(name: str, settings: MeterSettings) -> _Program[MeterPlace, object]:
    ...

def with_pauses(snapshot: MeterSnapshot, settings: MeterSettings, pauses: tuple) -> _Program[MeterSnapshot, object]:
    ...

def rewritten(name: str, place: MeterPlace, change: Callable) -> _Program[None, object]:
    ...

def process_meter_handler(place_name: str, settings: MeterSettings) -> _Handler:
    ...
