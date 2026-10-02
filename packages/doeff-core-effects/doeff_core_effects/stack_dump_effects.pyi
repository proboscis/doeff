"""stack_dump_effects.hy の公開面の型(全 thread の stack を書く見張りの effect と台帳 — 型検査のための宣言・実行時は stack_dump_effects.hy を読む)。

- defrecord は frozen で keyword だけの dataclass。
- defeffect は位置でも渡せる frozen の dataclass で、`EffectBase[答えの型]` の下位の型。
- defk は呼ぶと Program を返す(答えの型は Program の 1 つ目の引数)。
- 実装との食い違いは packages/doeff-core-effects/tests/test_hy_module_stubs.py が検める。
"""

from dataclasses import dataclass
from typing import Any

from doeff_vm import EffectBase

from doeff import Program

@dataclass(frozen=True)
class ArmStackDump(EffectBase[None]):
    at: float
    seconds: float

@dataclass(frozen=True)
class DisarmStackDump(EffectBase[None]):
    at: float

@dataclass(frozen=True)
class ReadStackDumps(EffectBase[tuple[float, ...]]):
    at: float

@dataclass(frozen=True, kw_only=True)
class StackDumpLedger:
    deadline: float | None
    written: tuple[float, ...]

def stack_dumps_at(ledger: StackDumpLedger, at: float) -> Program[StackDumpLedger, Any]: ...
