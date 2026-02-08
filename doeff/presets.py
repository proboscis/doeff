"""Handler presets for common doeff configurations.

SPEC-009 section 7: pre-built handler lists for typical use cases.
"""

from __future__ import annotations

from typing import Any

_cache: dict[str, list[Any]] = {}


def _get_preset() -> list[Any]:
    if "default" not in _cache:
        from doeff.rust_vm import default_handlers

        _cache["default"] = default_handlers()
    return list(_cache["default"])


def __getattr__(name: str) -> Any:
    if name in ("sync_preset", "async_preset"):
        return _get_preset()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["sync_preset", "async_preset"]
