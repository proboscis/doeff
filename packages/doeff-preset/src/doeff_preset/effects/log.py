"""Effect metadata for preset structured log display."""

from __future__ import annotations

from typing import Any

from doeff import WriterTellEffect

PRESET_LOG_EFFECT = WriterTellEffect


def is_structured_log_message(message: object) -> bool:
    """Return ``True`` for dictionary-based structured log payloads."""
    return isinstance(message, dict)


def is_structured_log_effect(effect: object) -> bool:
    """Return ``True`` for `WriterTellEffect` values with dictionary payloads."""
    return isinstance(effect, WriterTellEffect) and isinstance(effect.message, dict)


def coerce_structured_log(message: object) -> dict[str, Any] | None:
    """Return a typed structured-log payload if possible."""
    if not isinstance(message, dict):
        return None
    return {str(key): value for key, value in message.items()}


__all__ = [
    "PRESET_LOG_EFFECT",
    "coerce_structured_log",
    "is_structured_log_effect",
    "is_structured_log_message",
]
