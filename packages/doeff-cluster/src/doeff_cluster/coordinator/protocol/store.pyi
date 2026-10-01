"""coordinator/protocol/store.hy の公開面の型(型検査のための宣言 — 実行時は store.hy を読む・#2446)。

store.hy は Hy の module なので、pyright は中を読めず、名が全部 Unknown になる。調停ループの組を自分で組む使い手(使い手の repo の
模擬の世界の検)が durable-states と wal-store を使うと、書き手に直せない赤(Type of "durable_states" is unknown ほか)が出た。
ここで型を宣言する(request_bodies.pyi と同じ形)。

- Persist は凍った dataclass の EffectBase[None](欄 delta = キー → 新しい値・消えたキーは None)。
- DurableStore は置き場の形(persist の口だけ)。
- durable-states は handler の値。wal-store と memory-store は置き場を受けて handler を返す関数(handler の型は Any)。
"""

from dataclasses import dataclass
from typing import Any, Protocol

from doeff import EffectBase

MODULE_TAGS: dict[str, str]

@dataclass(frozen=True)
class Persist(EffectBase[None]):
    delta: dict[str, object]

class DurableStore(Protocol):
    def persist(self, delta: dict[str, object]) -> None: ...

durable_states: Any

def wal_store(store: DurableStore) -> Any: ...
def memory_store(log: list[dict[str, object]]) -> Any: ...
