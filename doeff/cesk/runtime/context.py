"""Runtime-level suspension handle for external thread completion.

This module provides the SuspensionHandle class that bridges CESK pure data
with runtime-level suspension support.

Note: The v2 HandlerContext is now in handler_frame.py. This module only
contains SuspensionHandle for runtime-level external thread signaling.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class SuspensionHandle(Generic[T]):
    """Runtime-provided handle for handlers to signal completion from external threads.

    This is NOT Promise - Promise is user-facing and works via Effects.
    SuspensionHandle is handler-facing and works via callbacks.
    Handlers ARE the impure boundary, so callbacks are OK here.
    """

    def __init__(
        self,
        on_complete: Callable[[T], None],
        on_fail: Callable[[BaseException], None],
    ) -> None:
        self._on_complete = on_complete
        self._on_fail = on_fail
        self._completed = False
        self._lock = threading.Lock()

    def complete(self, value: T) -> None:
        with self._lock:
            if self._completed:
                raise RuntimeError("SuspensionHandle already completed")
            self._completed = True
        self._on_complete(value)

    def fail(self, error: BaseException) -> None:
        with self._lock:
            if self._completed:
                raise RuntimeError("SuspensionHandle already completed")
            self._completed = True
        self._on_fail(error)

    @property
    def is_completed(self) -> bool:
        with self._lock:
            return self._completed


__all__ = [
    "SuspensionHandle",
]
