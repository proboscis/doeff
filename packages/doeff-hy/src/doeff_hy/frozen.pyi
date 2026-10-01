"""frozen.hy の公開面の型(型検査のための宣言 — 実行時は frozen.hy を読む・agora-redesign #2311・#2245)。

frozen.hy は Hy の module なので、pyright は中を読めず、`FrozenMap` と凍らせる関数が全部 Unknown になる(FrozenMap を返す
定義の答え・FrozenMap を欄に持つ値の読みに、書き手に直せない reportUnknown* が出る)。ここで型を宣言する。

- FrozenMap は文字列の鍵 → 値 V の変えられない写像。実装は `Mapping[str, V]` の部分型(鍵の型は文字列に決まっていて、
  型引数は値の型 V の 1 つ — `(get FrozenMap TableDecl)` の形)。型引数を書かない `FrozenMap` は FrozenMap[object]
  (中の値を問わない写像 — 実装の注記の多くがこの形)。変えられない写像なので値の型について共変
  (FrozenMap[int] は FrozenMap[object] として渡せる — Mapping と同じ)。
- freeze-json / thaw-json は JSON の値を深く凍らせる・戻す(どちらも値の形を問わず受けて返す — 答えは object)。
- frozen-json-object は写像を深く凍らせた FrozenMap(中の値は凍らせた JSON の値 — object)。
- frozen-map-of は写像を浅く写し取る(中の値はそのまま — 写像の値の型を運ぶ)。写像でない値は実行時に TypeError。
"""

from collections.abc import Iterable, Iterator, Mapping
from typing import overload

from typing_extensions import TypeVar

_V = TypeVar("_V", covariant=True, default=object)
_W = TypeVar("_W")

class FrozenMap(Mapping[str, _V]):
    """文字列の鍵 → 値の変えられない写像(作る時に写し取り、以後は変えられない)。"""

    def __init__(self, source: Mapping[str, _V] | Iterable[tuple[str, _V]] | None = None) -> None: ...
    def __getitem__(self, key: str) -> _V: ...
    def __iter__(self) -> Iterator[str]: ...
    def __len__(self) -> int: ...
    def __hash__(self) -> int: ...
    def updated(self, changes: Mapping[str, _W]) -> FrozenMap[_V | _W]: ...

def freeze_json(value: object) -> object: ...
def thaw_json(value: object) -> object: ...
def frozen_json_object(value: object, what: str) -> FrozenMap[object]: ...
@overload
def frozen_map_of(value: Mapping[str, _W], what: str) -> FrozenMap[_W]: ...
@overload
def frozen_map_of(value: object, what: str) -> FrozenMap[object]: ...
