"""Task-related effect handlers.

Handles effects for task management:
- SpawnEffect: Spawn a new task
- TaskJoinEffect: Wait for a task to complete
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.cesk.actions import CreateTask, WaitOnFuture
from doeff.cesk.handlers import HandlerContext, HandlerResult

if TYPE_CHECKING:
    from doeff.effects import SpawnEffect, TaskJoinEffect


def handle_spawn(effect: SpawnEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle SpawnEffect: spawn a new task.

    Creates a new task running the given program.
    The spawn returns a handle that can be used to join the task later.
    """
    return HandlerResult(
        (
            CreateTask(
                program=effect.program,
                env=ctx.env,
                store_snapshot=None,  # Share store with parent
            ),
        )
    )


def handle_task_join(effect: TaskJoinEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle TaskJoinEffect: wait for a task to complete.

    Waits for the task identified by effect.task to complete.
    Returns the task's result or raises its exception.
    """
    # The effect.task is a Task object that wraps the future_id
    # We need to extract the future_id and return a WaitOnFuture action
    return HandlerResult((WaitOnFuture(effect.task.future_id),))


__all__ = [
    "handle_spawn",
    "handle_task_join",
]
