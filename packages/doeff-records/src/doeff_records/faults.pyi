"""faults.hy の公開面の型(型検査のための宣言 — 実行時は faults.hy を読む)。

検の口の effect(置き場の版の更新・届かない状態・断りの故障)。答えの型は実装の頭の註のとおり
(AdvanceStoreEpoch = 新しい epoch・ほかは None)。
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from doeff import EffectBase
from doeff_records.values import Refused, Unreachable

@dataclass(frozen=True)
class AdvanceStoreEpoch(EffectBase[int]): ...

@dataclass(frozen=True)
class SetStoreOutage(EffectBase[None]):
    detail: str | None
    names: frozenset[str] | None = None

class StoreOperation(Enum):
    READ = "read"
    WRITE = "write"

@dataclass(frozen=True)
class StoreFault:
    names: frozenset[str]
    operation: StoreOperation
    answer: Refused | Unreachable
    lands: bool = False
    matching: Callable[[object], bool] | None = None

@dataclass(frozen=True)
class AddStoreFault(EffectBase[None]):
    fault: StoreFault

@dataclass(frozen=True)
class ClearStoreFaults(EffectBase[None]):
    names: frozenset[str] | None = None
