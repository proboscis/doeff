"""Memo (in-memory) effects for doeff."""

from typing import Any

from .base import Effect, create_effect_with_trace


class memo:
    """Memoization helper effects."""

    @staticmethod
    def get(key: Any) -> Effect:
        return create_effect_with_trace("memo.get", key)

    @staticmethod
    def put(key: Any, value: Any) -> Effect:
        return create_effect_with_trace("memo.put", {"key": key, "value": value})


def MemoGet(key: Any) -> Effect:
    return create_effect_with_trace("memo.get", key, skip_frames=3)


def MemoPut(key: Any, value: Any) -> Effect:
    return create_effect_with_trace("memo.put", {"key": key, "value": value}, skip_frames=3)


__all__ = [
    "memo",
    "MemoGet",
    "MemoPut",
]
