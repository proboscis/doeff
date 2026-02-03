"""Reader effect utilities.

Note: Ask and Local effects are handled by core_handler because they can nest
sub-programs and require access to the full continuation without forwarding pollution.
This module provides CircularAskError and constants used by core_handler.
"""

from __future__ import annotations

from typing import Any


class CircularAskError(Exception):
    """Raised when a circular dependency is detected in lazy Ask evaluation.

    This occurs when evaluating a Program value for Ask("key") requires
    asking for the same key (directly or indirectly), creating a cycle.

    Attributes:
        key: The Ask key where the cycle was detected.
    """

    def __init__(self, key: Any, message: str | None = None):
        self.key = key
        if message is None:
            message = f"Circular dependency detected for Ask({key!r})"
        super().__init__(message)


_ASK_IN_PROGRESS = object()


__all__ = ["CircularAskError", "_ASK_IN_PROGRESS"]
