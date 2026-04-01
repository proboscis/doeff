"""
Opaque collection — the result of Traverse.

Users should NOT access items directly. Use Traverse, Reduce, Zip, Inspect.
"""

from dataclasses import dataclass, field


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


class Collection:
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
    def valid_values(self) -> list:
        return [item.value for item in self._items if not item.failed]

    @property
    def all_items(self) -> list[ItemResult]:
        return list(self._items)

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        """Iterate over valid values only. For full access, use Inspect."""
        return iter(self.valid_values)

    def __repr__(self):
        valid = len(self.valid_items)
        failed = len(self.failed_items)
        return f"Collection({valid} valid, {failed} failed)"
