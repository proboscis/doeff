"""random_effects.hy の公開面の型(型検査のための宣言 — 実行時は random_effects.hy を読む・agora-redesign #2323)。

- defeffect は位置でも渡せる frozen の dataclass で、`EffectBase[答えの型]` の下位の型。`(<- x (RandomBytes n))` の x は bytes。
- 実装との食い違いは packages/doeff-core-effects/tests/test_hy_module_stubs.py が検める。
"""

from dataclasses import dataclass

from doeff_vm import EffectBase

@dataclass(frozen=True)
class RandomBytes(EffectBase[bytes]):
    count: int
