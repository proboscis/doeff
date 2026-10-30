"""worker/intent/drain_model.hy の公開面の型(型検査のための宣言 — 実行時は drain_model.hy を読む)。

drain_model.hy は Hy の module なので、pyright は中を読めず、名が全部 Unknown になる。drain の頼みを型のある intent AskDrain に
した時(#2541)、使い手の repo の模擬の世界が AskDrain に答えると、書き手に直せない赤(Type of "AskDrain" is unknown ほか)が出た。
ここで型を宣言する(launch.pyi・request_bodies.pyi と同じ形)。

- effect(CoordinatorCall・AskDrain)は凍った dataclass の EffectBase[答えの型]。答え = {"status" int "body" dict}、届かなければ
  {"error" 理由の文}(drain_requests.call-answer)。
"""

from dataclasses import dataclass

from doeff import EffectBase

MODULE_TAGS: dict[str, str]

@dataclass(frozen=True)
class CoordinatorCall(EffectBase[dict[str, object]]):
    method: str
    path: str
    body: dict[str, object] | None = ...

@dataclass(frozen=True)
class AskDrain(EffectBase[dict[str, object]]):
    name: str
    ttl_seconds: float
    own_boot: str | None = ...
