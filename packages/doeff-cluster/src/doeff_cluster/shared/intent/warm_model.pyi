"""warm_model.hy の公開面の型(型検査のための宣言 — 実行時は warm_model.hy を読む・#2564)。

warm_model.hy は Hy の module なので、pyright は中を読めず、`doeff_cluster.shared.intent.warm_model` の名が全部 Unknown になる。
構築関数 warm-runtime-env(core/warm_rules.pyi)の答えの型 WarmAnswer がここに在るので、構築関数を呼ぶ使い手の file ごとに、
書き手に直せない赤(Type of "warm_runtime_env" is partially unknown ほか)が出た。ここで型を宣言する(runtime_env_model.pyi と同じ形)。

- defrecord(WarmFailure・WarmState・WarmUnreachable)は凍った・キーワード引数だけの dataclass。欄の型は warm_model.hy の注記に
  合わせる(注記が素の tuple の所は、warm_rules の warm-state-of-json が入れる要素の型で書く)。
- effect(WarmRuntimeEnv・ReadWarmState)は凍った dataclass の EffectBase[答えの型]。
"""

from dataclasses import dataclass
from typing import TypeAlias

from doeff import EffectBase

from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv

WARM_KEY_LENGTH: int

@dataclass(frozen=True, kw_only=True)
class WarmFailure:
    """温める準備の失敗 1 つ: worker の名・EnvFailureKind の値の綴り・理由・一時か。"""

    worker: str
    kind: str
    detail: str
    retryable: bool

@dataclass(frozen=True, kw_only=True)
class WarmState:
    """温める表の行 1 つの今の姿(ready / preparing = worker の名・failed = 準備に失敗した worker)。"""

    key: str
    ready: tuple[str, ...]
    preparing: tuple[str, ...]
    failed: tuple[WarmFailure, ...]
    until_ms: int

@dataclass(frozen=True, kw_only=True)
class WarmUnreachable:
    """coordinator の /warm に届かなかった(温まっていないと同じに読む)。"""

    detail: str

WarmAnswer: TypeAlias = WarmState | WarmUnreachable

@dataclass(frozen=True)
class WarmRuntimeEnv(EffectBase[WarmAnswer]):
    """env を needs の合う worker で温めるよう頼む。作り手は構築関数 warm_rules.warm-runtime-env を通す。"""

    env: RuntimeEnv
    needs: frozenset[str]
    ttl_seconds: float
    holder: str

@dataclass(frozen=True)
class ReadWarmState(EffectBase[WarmAnswer]):
    """温める表の行 key の今の姿を読む。"""

    key: str
