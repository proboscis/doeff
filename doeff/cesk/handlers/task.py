"""Task and spawn effect handlers: Spawn, TaskJoin, IO."""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.cesk.actions import CreateTask, PerformIO, Resume

if TYPE_CHECKING:
    from doeff.cesk.step import HandlerContext
    from doeff.effects import IOPerformEffect, SpawnEffect, TaskJoinEffect


def handle_spawn(effect: SpawnEffect, ctx: HandlerContext) -> tuple[CreateTask, ...]:
    return (CreateTask(effect.program),)


def handle_task_join(effect: TaskJoinEffect, ctx: HandlerContext) -> tuple[Resume, ...]:
    task = effect.task
    return (Resume(task._handle),)


def handle_io(effect: IOPerformEffect, ctx: HandlerContext) -> tuple[PerformIO, ...]:
    return (PerformIO(effect.action),)


__all__ = [
    "handle_spawn",
    "handle_task_join",
    "handle_io",
]
