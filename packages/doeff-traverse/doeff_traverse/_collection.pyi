"""Types for the public surface of collection.py (declarations for type checking).

Collection is a sequence of per-item results and carries the valid-item value type `_V`. The Traverse handler puts only the answer of f (the `_V` of `Program[_V, …]`)
into valid item values, so `valid_values` is `list[_V]`. The value of a failed item is an exception or (for an item dropped by When)
the original item, so the value of the item record ItemResult stays object.
"""

from collections.abc import Iterable, Iterator
from typing import Generic, TypeVar

_V = TypeVar("_V", covariant=True)
_W = TypeVar("_W")

class ItemResult:
    """The result of one item, and the history of that item."""

    index: int
    value: object
    failed: bool
    history: list[HistoryEntry]
    def __init__(
        self, index: int, value: object, failed: bool = False, history: list[HistoryEntry] = ...
    ) -> None: ...

class HistoryEntry:
    """One line of an item's history."""

    stage: str | None
    event: str
    detail: str | None
    attempt: int
    def __init__(
        self, stage: str | None = None, event: str = "", detail: str | None = None, attempt: int = 1
    ) -> None: ...

class Collection(Generic[_V]):
    """A sequence ordered by item position whose contents are not read directly (the answer of Traverse, Zip, SortBy, Take)."""

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
    def flatten(self: Collection[Collection[_W]]) -> Collection[_W]:
        """Turns a Collection whose valid values are Collections into a single Collection of the inner items laid out in order (expansion of nested From in for/do)."""
        ...
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[_V]: ...
