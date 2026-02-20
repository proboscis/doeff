"""Handler presets for common doeff configurations.

SPEC-009 section 7: pre-built handler lists for typical use cases.
"""

from __future__ import annotations

from typing import Any

_cache: dict[str, list[Any]] = {}


def _get_sync_preset() -> list[Any]:
    if "sync" not in _cache:
        from doeff.rust_vm import default_handlers

        _cache["sync"] = default_handlers()
    return list(_cache["sync"])


def _get_async_preset() -> list[Any]:
    if "async" not in _cache:
        from doeff.rust_vm import default_async_handlers

        _cache["async"] = default_async_handlers()
    return list(_cache["async"])


def __getattr__(name: str) -> Any:  # nosemgrep: doeff-no-typing-any-in-public-api
    if name == "sync_preset":
        return _get_sync_preset()
    if name == "async_preset":
        return _get_async_preset()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["sync_preset", "async_preset"]
