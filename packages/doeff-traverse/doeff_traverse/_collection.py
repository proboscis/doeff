"""
Opaque collection — the result of Traverse.

Users should NOT access items directly. Use Traverse, Reduce, Zip, Inspect.
"""

from dataclasses import dataclass, field
from typing import Generic, TypeVar

#: 有効な件の値の型。型の宣言は collection.pyi が持ち、実行時は `Collection[T]` と書けるようにだけ Generic を継ぐ。
_V = TypeVar("_V", covariant=True)


@dataclass
class ItemResult:
    """Per-item result with execution history."""
    index: int
    value: object
    failed: bool = False
    history: list = field(default_factory=list)


@dataclass
class HistoryEntry:
    """One entry in an item's execution history."""
    stage: str | None = None
    event: str = ""  # "ok", "retried", "failed", "skipped"
    detail: str | None = None
    attempt: int = 1


class Collection(Generic[_V]):
    """Opaque item-indexed collection.

    Tracks values and per-item history. Not meant to be accessed directly
    by user logic — use Traverse/Reduce/Zip/Inspect effects instead.
    """

    def __init__(self, items: list[ItemResult], source_keys: list | None = None):
        self._items = items
        self._source_keys = source_keys or list(range(len(items)))

    @classmethod
    def from_values(cls, values: list, stage: str | None = None) -> "Collection":
        """Create a Collection from a plain list of values."""
        items = [
            ItemResult(
                index=i,
                value=v,
                history=[HistoryEntry(stage=stage, event="ok")],
            )
            for i, v in enumerate(values)
        ]
        return cls(items)

    @classmethod
    def from_iterable(cls, iterable) -> "Collection":
        """Wrap a plain iterable as a Collection (no history)."""
        if isinstance(iterable, Collection):
            return iterable
        values = list(iterable)
        return cls([ItemResult(index=i, value=v) for i, v in enumerate(values)])

    @property
    def valid_items(self) -> list[ItemResult]:
        return [item for item in self._items if not item.failed]

    @property
    def failed_items(self) -> list[ItemResult]:
        return [item for item in self._items if item.failed]

    @property
    def errors(self) -> list[BaseException]:
        """Exceptions of the items that failed, in item order.

        A failed item's value is its exception, except a skipped item (a When guard),
        whose value is the original item — so failed_items[i].value is only object.
        Read this to raise or report the first failure with its type known.
        """
        return [item.value for item in self._items if item.failed and isinstance(item.value, BaseException)]

    @property
    def valid_values(self) -> list:
        return [item.value for item in self._items if not item.failed]

    @property
    def all_items(self) -> list[ItemResult]:
        return list(self._items)

    def flatten(self) -> "Collection":
        """Splice a Collection whose valid values are Collections into one Collection.

        for/do with nested From binds answers one item per combination, like a list
        comprehension: the outer Traverse answers a Collection of the
        inner Collections, and the expansion flattens it here. An inner item keeps its
        value and failure, and its history follows the outer item's history. A failed or
        skipped outer item has no inner Collection and stays one item. Indices are
        renumbered in order.
        """
        spliced = [part for outer in self._items for part in _spliced(outer)]
        return Collection([
            ItemResult(index=index, value=value, failed=failed, history=history)
            for index, (value, failed, history) in enumerate(spliced)
        ])

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        """Iterate over valid values only. For full access, use Inspect."""
        return iter(self.valid_values)

    def __repr__(self):
        valid = len(self.valid_items)
        failed = len(self.failed_items)
        return f"Collection({valid} valid, {failed} failed)"


def _spliced(outer: ItemResult) -> list[tuple[object, bool, list]]:
    """The (value, failed, history) entries one outer item contributes to Collection.flatten.

    A failed or skipped outer item stays itself; a valid one gives every item of its inner
    Collection, with the outer history in front.
    """
    if outer.failed:
        return [(outer.value, True, list(outer.history))]
    if not isinstance(outer.value, Collection):
        raise TypeError(f"Collection.flatten: item {outer.index} is {type(outer.value).__name__}, not a Collection")
    return [(inner.value, inner.failed, outer.history + inner.history) for inner in outer.value.all_items]
