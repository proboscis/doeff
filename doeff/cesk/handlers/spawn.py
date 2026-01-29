"""Spawn effect handlers for background task execution.

This module implements handlers for Spawn/Task effects as specified
in SPEC-EFF-005-concurrency.md.

Design Decisions:
1. Store semantics: Snapshot at spawn time (isolated)
2. Error handling: Exception stored in Task until join
3. Cancellation: Follow asyncio conventions
"""

from __future__ import annotations

from doeff.cesk.frames import FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store
from doeff.effects.spawn import (
    SpawnEffect,
    TaskCancelEffect,
    TaskIsDoneEffect,
)
from doeff.effects.wait import WaitEffect


def handle_spawn(
    effect: SpawnEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    """Handle SpawnEffect by creating a Task handle.
    
    Note: The actual spawning is handled by the runtime (AsyncRuntime).
    This handler is a placeholder that returns the effect unchanged,
    signaling to the runtime that it should handle this effect specially.
    
    For synchronous execution (SyncRuntime), spawn is not supported
    and will raise an error.
    """
    # This should not be reached - the runtime should intercept SpawnEffect
    # before it gets to the handler dispatch
    raise NotImplementedError(
        "SpawnEffect must be handled by the runtime, not the default handler. "
        "Use AsyncRuntime for spawn support."
    )


def handle_wait(
    effect: WaitEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    """Handle WaitEffect by waiting for task completion.
    
    Note: The actual wait is handled by the runtime (AsyncRuntime).
    This handler is a placeholder.
    """
    # This should not be reached - the runtime should intercept WaitEffect
    raise NotImplementedError(
        "WaitEffect must be handled by the runtime, not the default handler. "
        "Use AsyncRuntime for spawn support."
    )


def handle_task_cancel(
    effect: TaskCancelEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    """Handle TaskCancelEffect by requesting task cancellation.
    
    Note: The actual cancellation is handled by the runtime (AsyncRuntime).
    This handler is a placeholder.
    """
    # This should not be reached - the runtime should intercept TaskCancelEffect
    raise NotImplementedError(
        "TaskCancelEffect must be handled by the runtime, not the default handler. "
        "Use AsyncRuntime for spawn support."
    )


def handle_task_is_done(
    effect: TaskIsDoneEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    """Handle TaskIsDoneEffect by checking task completion status.
    
    Note: The actual status check is handled by the runtime (AsyncRuntime).
    This handler is a placeholder.
    """
    # This should not be reached - the runtime should intercept TaskIsDoneEffect
    raise NotImplementedError(
        "TaskIsDoneEffect must be handled by the runtime, not the default handler. "
        "Use AsyncRuntime for spawn support."
    )


__all__ = [
    "handle_spawn",
    "handle_task_cancel",
    "handle_task_is_done",
    "handle_wait",
]
