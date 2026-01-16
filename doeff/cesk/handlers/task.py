"""Task management effect handlers: Task, Join, Gather, Race."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.frames import GatherFrame
from doeff.cesk.state import (
    CreateTaskRequest,
    ProgramControl,
    ReadyStatus,
    RequestingStatus,
    TaskState,
    ValueControl,
)

if TYPE_CHECKING:
    from doeff.cesk.frames import Kontinuation
    from doeff.cesk.types import Environment, Store


def handle_task(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    return TaskState(
        control=ValueControl(None),
        env=env,
        store=store,
        kontinuation=k,
        status=RequestingStatus(CreateTaskRequest(effect.program)),
    )


def handle_join(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    from doeff.cesk.state import BlockedStatus, TaskCondition
    
    task_id = effect.task_id
    return TaskState(
        control=ValueControl(None),
        env=env,
        store=store,
        kontinuation=k,
        status=BlockedStatus(TaskCondition(task_id)),
    )


def handle_gather(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    programs = list(effect.programs)
    if not programs:
        return TaskState(
            control=ValueControl([]),
            env=env,
            store=store,
            kontinuation=k,
            status=ReadyStatus(None),
        )
    first, *rest = programs
    return TaskState(
        control=ProgramControl(first),
        env=env,
        store=store,
        kontinuation=[GatherFrame(rest, [], env)] + k,
        status=ReadyStatus(None),
    )


def handle_race(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    programs = list(effect.programs)
    if not programs:
        raise ValueError("race requires at least one program")
    
    return TaskState(
        control=ValueControl(None),
        env=env,
        store=store,
        kontinuation=k,
        status=RequestingStatus(CreateTaskRequest(programs[0])),
    )


__all__ = [
    "handle_gather",
    "handle_join",
    "handle_race",
    "handle_task",
]
