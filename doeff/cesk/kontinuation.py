"""Kontinuation helpers for the unified CESK machine.

This module provides helper functions for manipulating the continuation stack (K):
- resume: Resume a continuation with a value
- throw: Throw an error into a continuation
- push_frame: Push a frame onto the continuation
- unwind: Process a value/error through the continuation stack
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.types import Environment, Store
from doeff.cesk.frames import (
    Kontinuation,
    Frame,
    FrameResult,
    ContinueValue,
    ContinueError,
    ContinueProgram,
    ContinueGenerator,
    ReturnFrame,
    LocalFrame,
    InterceptFrame,
    ListenFrame,
    GatherFrame,
    SafeFrame,
)

if TYPE_CHECKING:
    from doeff.cesk.state import TaskState, Control


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
) -> FrameResult:
    """Unwind the continuation stack with a value.

    Processes the value through the topmost frame's on_value handler.
    If the continuation is empty, returns a ContinueValue with empty k.

    Args:
        value: The value to process
        env: Current environment
        store: Shared store
        k: Continuation stack

    Returns:
        FrameResult from processing the value
    """
    if not k:
        return ContinueValue(value=value, env=env, store=store, k=[])

    frame = k[0]
    k_rest = k[1:]

    return frame.on_value(value, env, store, k_rest)


def unwind_error(
    error: BaseException,
    env: Environment,
    store: Store,
    k: Kontinuation,
    captured_traceback: Any | None = None,
) -> FrameResult:
    """Unwind the continuation stack with an error.

    Processes the error through the topmost frame's on_error handler.
    If the continuation is empty, returns a ContinueError with empty k.

    Args:
        error: The error to process
        env: Current environment
        store: Shared store
        k: Continuation stack
        captured_traceback: Optional captured traceback

    Returns:
        FrameResult from processing the error
    """
    if not k:
        return ContinueError(
            error=error,
            env=env,
            store=store,
            k=[],
            captured_traceback=captured_traceback,
        )

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

    Args:
        k: The continuation stack to search

    Returns:
        Index of the first InterceptFrame, or -1 if none found
    """
    idx, _ = find_frame(k, InterceptFrame)
    return idx


def has_intercept_frame(k: Kontinuation) -> bool:
    """Check if the continuation contains an InterceptFrame.

    Args:
        k: The continuation stack to search

    Returns:
        True if an InterceptFrame exists
    """
    return has_frame(k, InterceptFrame)


def find_safe_frame_index(k: Kontinuation) -> int:
    """Find the index of the first SafeFrame in the continuation.

    Args:
        k: The continuation stack to search

    Returns:
        Index of the first SafeFrame, or -1 if none found
    """
    idx, _ = find_frame(k, SafeFrame)
    return idx


def has_safe_frame(k: Kontinuation) -> bool:
    """Check if the continuation contains a SafeFrame.

    Args:
        k: The continuation stack to search

    Returns:
        True if a SafeFrame exists
    """
    return has_frame(k, SafeFrame)


def get_intercept_transforms(k: Kontinuation) -> list[Any]:
    """Get all intercept transforms from the continuation stack.

    Collects transforms from all InterceptFrames in order (outer to inner).

    Args:
        k: The continuation stack to search

    Returns:
        List of transform functions
    """
    transforms = []
    for frame in k:
        if isinstance(frame, InterceptFrame):
            transforms.extend(frame.transforms)
    return transforms


def apply_intercept_chain(
    k: Kontinuation,
    effect: Any,
) -> Any:
    """Apply all intercept transforms to an effect.

    Applies transforms from InterceptFrames in order (outer to inner).
    If any transform returns None, the original effect is returned.
    If any transform returns a Program, that program is returned.

    Args:
        k: The continuation stack
        effect: The effect to transform

    Returns:
        The transformed effect, a Program, or the original effect
    """
    from doeff.program import ProgramBase

    current = effect

    for frame in k:
        if not isinstance(frame, InterceptFrame):
            continue

        for transform in frame.transforms:
            result = transform(current)

            if result is None:
                # Transform declined to handle this effect
                continue

            if isinstance(result, ProgramBase):
                # Transform returned a program replacement
                return result

            # Transform returned a modified effect
            current = result

    return current


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
    # Stack operations
    "push_frame",
    "pop_frame",
    # Unwinding
    "unwind_value",
    "unwind_error",
    # Frame finding
    "find_frame",
    "has_frame",
    "find_intercept_frame_index",
    "has_intercept_frame",
    "find_safe_frame_index",
    "has_safe_frame",
    # Intercept helpers
    "get_intercept_transforms",
    "apply_intercept_chain",
    # Utilities
    "continuation_depth",
    "split_at_safe",
]
