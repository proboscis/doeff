"""readiness_model.hy の公開面の型(型検査のための宣言 — 実行時は readiness_model.hy を読む・#2777)。

readiness_model.hy は Hy の module なので、pyright は中を読めず、`doeff_cluster.shared.intent.readiness_model` の名が全部 Unknown になる。
準備できたの報告 ReportReady を出す使い手の service の file ごとに、書き手に直せない赤
(Type of "ReportReady" is unknown・Argument type is unknown)が出た。ここで型を宣言する(warm_model.pyi と同じ形)。

- effect の ReportReady は凍った dataclass の EffectBase[None](欄は ready・reason・role の順 — 位置でも名でも渡せる)。
  記録の仕方の印 __record_spec__ は class の値で、欄ではないので宣言しない。
- 実装との食い違いは packages/doeff-cluster/tests/test_readiness_static_types.py が検める。
"""

from dataclasses import dataclass
from typing import TypeAlias

from doeff import EffectBase

ROLE_ACTIVE: str
ROLE_STANDBY: str
HANDOFF_TIMEOUT_SECONDS: int
READINESS_KEYS: tuple[str, ...]
REASON_KEPT_CHARS: int
JsonField: TypeAlias = dict[str, object] | list[object] | str | int | float | bool | None

@dataclass(frozen=True)
class ReportReady(EffectBase[None]):
    """準備できた(ready 真)/できていない(偽)の報告。reason = 人が読む理由・role = active か standby。答えは None。"""

    ready: bool
    reason: str = ""
    role: str = ...
