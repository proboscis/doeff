"""meter_effects.hy の公開面の型(計器の effect と値 — 型検査のための宣言・実行時は meter_effects.hy を読む)。

meter_effects.hy は Hy の module なので、型の宣言が無いと pyright は中を読めず、計器の答え手(memory_meter.pyi)の引数
MeterSettings も、使う側が作る `(MeterSettings)` も Unknown になる。ここで型を宣言する。

- defrecord は frozen で keyword だけの dataclass。
- defeffect は位置でも渡せる frozen の dataclass で、`EffectBase[答えの型]` の下位の型。
- defk は呼ぶと Program を返す(答えの型は Program の 1 つ目の引数)。
- 実装との食い違いは packages/doeff-core-effects/tests/test_hy_module_stubs.py が検める。
"""

from dataclasses import dataclass
from typing import Any

from doeff_hy.frozen import FrozenMap
from doeff_vm import EffectBase

from doeff import Program

# --- 値 ---

@dataclass(frozen=True, kw_only=True)
class SecondsTotal:
    total: float
    count: int

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
    buckets: tuple[MeterBucket, ...] = ()
    inf_label: str = "le_inf"
    gc_pause_name: str | None = None

# --- effect ---

@dataclass(frozen=True)
class CountMetric(EffectBase[None]):
    name: str
    amount: float = 1.0

@dataclass(frozen=True)
class ObserveSeconds(EffectBase[None]):
    name: str
    seconds: float

@dataclass(frozen=True)
class SetGauge(EffectBase[None]):
    name: str
    value: float

@dataclass(frozen=True)
class ReadMeter(EffectBase[MeterSnapshot]): ...

# --- 断面の計算(純関数) ---

def counted(snapshot: MeterSnapshot, name: str, amount: float) -> Program[MeterSnapshot, Any]: ...
def gauged(snapshot: MeterSnapshot, name: str, value: float) -> Program[MeterSnapshot, Any]: ...
def observed(
    snapshot: MeterSnapshot, settings: MeterSettings, name: str, seconds: float
) -> Program[MeterSnapshot, Any]: ...
