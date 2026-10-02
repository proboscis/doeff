"""latest_effects.hy の公開面の型(最新の値の effect — 型検査のための宣言・実行時は latest_effects.hy を読む)。

latest_effects.hy は Hy の module なので、型の宣言が無いと pyright は中を読めず、PublishLatest・ReadLatest を出す
使い手の Program が全部 Unknown になる(agora-redesign #2746 — 答え手の memory_latest.pyi はあるのに effect の宣言が漏れていた)。

- defeffect は位置でも渡せる frozen の dataclass で、`EffectBase[答えの型]` の下位の型。
- 実装との食い違いは packages/doeff-core-effects/tests/test_hy_module_stubs.py が検める。
"""

from dataclasses import dataclass

from doeff_vm import EffectBase

@dataclass(frozen=True)
class PublishLatest(EffectBase[None]):
    value: object

@dataclass(frozen=True)
class ReadLatest(EffectBase[object | None]):
    kind: type
