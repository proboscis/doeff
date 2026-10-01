"""store_choice.hy の公開面の型(型検査のための宣言 — 実行時は store_choice.hy を読む・agora-redesign #2311)。

prepare-of = (schema prefix host) → 「書き手の名 → 記録の handler」の関数を返す Program を作る関数・readiness = () → 置き場に
届けば True の Program を作る関数(None = 用意が済めば ready)。実装の注記は素の Callable なので、ここも引数と答えを問わない
Callable の形に留める(呼び手が渡す関数の形は置き場ごとに違う — PG-STORE は defk・memory は partial)。
"""

from collections.abc import Callable
from dataclasses import dataclass

@dataclass(frozen=True)
class StoreChoice:
    prepare_of: Callable[..., object]
    readiness: Callable[..., object] | None = None
