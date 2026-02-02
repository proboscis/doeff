"""Kontinuation helpers for the unified CESK machine.

This module provides helper functions for manipulating the continuation stack (K):
- resume: Resume a continuation with a value
- throw: Throw an error into a continuation
- push_frame: Push a frame onto the continuation
- unwind: Process a value/error through the continuation stack

Per SPEC-CESK-003: InterceptFrame and SafeFrame have been removed.
Intercept-related functions are kept for backwards compatibility but do minimal work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.frames import (
    Frame,
    Kontinuation,
)
from doeff.cesk.state import CESKState
from doeff.cesk.types import Environment, Store

if TYPE_CHECKING:
    pass


def push_frame(k: Kontinuation, frame: Frame) -> Kontinuation:
    """Push a frame onto the continuation stack.

    Args:
        k: The current continuation stack
        frame: The frame to push

    Returns:
        New continuation with frame at the front (top of stack)
    """
    return [frame] + list(k)


def pop_frame(k: Kontinuation) -> tuple[Frame | None, Kontinuation]:
    """Pop a frame from the continuation stack.

    Args:
        k: The current continuation stack

    Returns:
        Tuple of (popped frame or None, remaining continuation)
    """
    if not k:
        return None, []
    return k[0], k[1:]


def unwind_value(
    value: Any,
    env: Environment,
    store: Store,
    k: Kontinuation,
) -> CESKState:
    """Unwind the continuation stack with a value.

    Processes the value through the topmost frame's on_value handler.
    If the continuation is empty, returns a CESKState with the value and empty k.

    Args:
        value: The value to process
        env: Current environment
        store: Shared store
        k: Continuation stack

    Returns:
        CESKState from processing the value
    """
    if not k:
        return CESKState.with_value(value, env, store, [])

    frame = k[0]
    k_rest = k[1:]

    return frame.on_value(value, env, store, k_rest)


def unwind_error(
    error: BaseException,
    env: Environment,
    store: Store,
    k: Kontinuation,
    captured_traceback: Any | None = None,
) -> CESKState:
    """Unwind the continuation stack with an error.

    Processes the error through the topmost frame's on_error handler.
    If the continuation is empty, returns a CESKState with the error and empty k.

    Args:
        error: The error to process
        env: Current environment
        store: Shared store
        k: Continuation stack
        captured_traceback: Optional captured traceback

    Returns:
        CESKState from processing the error
    """
    if not k:
        return CESKState.with_error(error, env, store, [], captured_traceback)

    frame = k[0]
    k_rest = k[1:]

    return frame.on_error(error, env, store, k_rest)


def find_frame(k: Kontinuation, frame_type: type) -> tuple[int, Frame | None]:
    """Find the first frame of a given type in the continuation.

    Args:
        k: The continuation stack to search
        frame_type: The type of frame to find

    Returns:
        Tuple of (index, frame) or (-1, None) if not found
    """
    for i, frame in enumerate(k):
        if isinstance(frame, frame_type):
            return i, frame
    return -1, None


def has_frame(k: Kontinuation, frame_type: type) -> bool:
    """Check if the continuation contains a frame of the given type.

    Args:
        k: The continuation stack to search
        frame_type: The type of frame to find

    Returns:
        True if a frame of the type exists
    """
    idx, _ = find_frame(k, frame_type)
    return idx >= 0


def find_intercept_frame_index(k: Kontinuation) -> int:
    """Find the index of the first InterceptFrame in the continuation.

    DEPRECATED: InterceptFrame has been removed per SPEC-CESK-003.
    Always returns -1 for backwards compatibility.
    """
    return -1


def has_intercept_frame(k: Kontinuation) -> bool:
    """Check if the continuation contains an InterceptFrame.

    DEPRECATED: InterceptFrame has been removed per SPEC-CESK-003.
    Always returns False for backwards compatibility.
    """
    return False


def find_safe_frame_index(k: Kontinuation) -> int:
    """Find the index of the first SafeFrame in the continuation.

    DEPRECATED: SafeFrame has been removed per SPEC-CESK-003.
    Always returns -1 for backwards compatibility.
    """
    return -1


def has_safe_frame(k: Kontinuation) -> bool:
    """Check if the continuation contains a SafeFrame.

    DEPRECATED: SafeFrame has been removed per SPEC-CESK-003.
    Always returns False for backwards compatibility.
    """
    return False


def get_intercept_transforms(k: Kontinuation) -> list[Any]:
    """Get all intercept transforms from the continuation stack.

    DEPRECATED: InterceptFrame has been removed per SPEC-CESK-003.
    Always returns empty list for backwards compatibility.
    """
    return []


def apply_intercept_chain(
    k: Kontinuation,
    effect: Any,
) -> Any:
    """Apply all intercept transforms to an effect.

    DEPRECATED: InterceptFrame has been removed per SPEC-CESK-003.
    Always returns the original effect for backwards compatibility.
    """
    return effect


def continuation_depth(k: Kontinuation) -> int:
    """Get the depth (number of frames) of the continuation.

    Args:
        k: The continuation stack

    Returns:
        Number of frames in the continuation
    """
    return len(k)


def split_at_safe(k: Kontinuation) -> tuple[Kontinuation, Kontinuation]:
    """Split the continuation at the first SafeFrame.

    Returns the continuation before and including the SafeFrame, and after.

    Args:
        k: The continuation stack

    Returns:
        Tuple of (before_and_including_safe, after_safe)
    """
    idx = find_safe_frame_index(k)
    if idx < 0:
        return k, []
    return k[: idx + 1], k[idx + 1 :]


__all__ = [
    "apply_intercept_chain",
    # Utilities
    "continuation_depth",
    # Frame finding
    "find_frame",
    "find_intercept_frame_index",
    "find_safe_frame_index",
    # Intercept helpers
    "get_intercept_transforms",
    "has_frame",
    "has_intercept_frame",
    "has_safe_frame",
    "pop_frame",
    # Stack operations
    "push_frame",
    "split_at_safe",
    "unwind_error",
    # Unwinding
    "unwind_value",
]
