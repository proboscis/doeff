"""Utility helpers for the doeff library."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class BoundedLog(list):
    """List-like buffer that keeps at most ``max_entries`` items."""

    __slots__ = ("_max_entries",)

    def __init__(
        self,
        iterable: Iterable[Any] | None = None,
        *,
        max_entries: int | None = None,
    ) -> None:
        super().__init__(iterable or [])
        self._max_entries: int | None = None
        self.set_max_entries(max_entries)

    @property
    def max_entries(self) -> int | None:
        """Return the maximum number of entries retained (``None`` means unbounded)."""

        return self._max_entries

    def set_max_entries(self, max_entries: int | None) -> None:
        """Update the retention limit and trim existing entries if required."""

        if max_entries is not None and max_entries < 0:
            raise ValueError("max_entries must be >= 0 or None")
        self._max_entries = max_entries
        self._trim()

    def append(self, item: Any) -> None:  # type: ignore[override]
        super().append(item)
        self._trim()

    def extend(self, iterable: Iterable[Any]) -> None:  # type: ignore[override]
        super().extend(iterable)
        self._trim()

    def __iadd__(self, iterable: Iterable[Any]):  # type: ignore[override]
        super().__iadd__(iterable)
        self._trim()
        return self

    def insert(self, index: int, item: Any) -> None:  # type: ignore[override]
        super().insert(index, item)
        self._trim()

    def copy(self) -> BoundedLog:  # type: ignore[override]
        """Return a shallow copy that preserves the retention limit."""

        return type(self)(self, max_entries=self._max_entries)

    def spawn_empty(self) -> BoundedLog:
        """Create an empty buffer with the same retention semantics."""

        return type(self)(max_entries=self._max_entries)

    def _trim(self) -> None:
        if self._max_entries is None:
            return
        overflow = len(self) - self._max_entries
        if overflow > 0:
            del self[:overflow]

__all__ = [
    "BoundedLog",
]
