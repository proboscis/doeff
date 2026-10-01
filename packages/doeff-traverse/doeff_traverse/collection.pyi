"""collection.py の公開面の型(型検査のための宣言・agora-redesign #2321)。

Collection は件ごとの結果の列で、有効な件の値の型 `_V` を運ぶ。Traverse の handler は f の答え(`Program[_V, …]` の
`_V`)だけを有効な件の値に置くので、`valid_values` は `list[_V]`。失敗した件の value は例外か(When で落ちた件は)
元の件なので、件の記録 ItemResult の value は object のまま。
"""

from collections.abc import Iterable, Iterator
from typing import Generic, TypeVar

_V = TypeVar("_V", covariant=True)
_W = TypeVar("_W")

class ItemResult:
    """件 1 つの結果と、その件の履歴。"""

    index: int
    value: object
    failed: bool
    history: list[HistoryEntry]
    def __init__(
        self, index: int, value: object, failed: bool = False, history: list[HistoryEntry] = ...
    ) -> None: ...

class HistoryEntry:
    """件の履歴の 1 行。"""

    stage: str | None
    event: str
    detail: str | None
    attempt: int
    def __init__(
        self, stage: str | None = None, event: str = "", detail: str | None = None, attempt: int = 1
    ) -> None: ...

class Collection(Generic[_V]):
    """件の位置で並ぶ、中を直に読まない列(Traverse・Zip・SortBy・Take の答え)。"""

    def __init__(self, items: list[ItemResult], source_keys: list[object] | None = None) -> None: ...
    @classmethod
    def from_values(cls, values: Iterable[_W], stage: str | None = None) -> Collection[_W]: ...
    @classmethod
    def from_iterable(cls, iterable: Iterable[_W]) -> Collection[_W]: ...
    @property
    def valid_items(self) -> list[ItemResult]: ...
    @property
    def failed_items(self) -> list[ItemResult]: ...
    @property
    def errors(self) -> list[BaseException]: ...
    @property
    def valid_values(self) -> list[_V]: ...
    @property
    def all_items(self) -> list[ItemResult]: ...
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[_V]: ...
