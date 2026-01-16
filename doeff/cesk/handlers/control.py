"""Control flow effect handlers: Local, Safe, Listen, Intercept."""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff._vendor import FrozenDict
from doeff.cesk.frames import (
    ContinueProgram,
    InterceptFrame,
    ListenFrame,
    LocalFrame,
    SafeFrame,
)

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk.frames import FrameResult
    from doeff.cesk.state import TaskState
    from doeff.cesk.types import Store


def handle_local(effect: EffectBase, task: TaskState, store: Store) -> FrameResult:
    from doeff.effects.reader import LocalEffect

    if not isinstance(effect, LocalEffect):
        raise TypeError(f"Expected LocalEffect, got {type(effect).__name__}")

    new_env = task.env | FrozenDict(effect.env_update)
    sub_program = effect.sub_program

    return ContinueProgram(
        program=sub_program,
        env=new_env,
        store=store,
        k=[LocalFrame(task.env)] + task.kontinuation,
    )


def handle_safe(effect: EffectBase, task: TaskState, store: Store) -> FrameResult:
    from doeff.effects.result import ResultSafeEffect

    if not isinstance(effect, ResultSafeEffect):
        raise TypeError(f"Expected ResultSafeEffect, got {type(effect).__name__}")

    sub_program = effect.sub_program

    return ContinueProgram(
        program=sub_program,
        env=task.env,
        store=store,
        k=[SafeFrame(task.env)] + task.kontinuation,
    )


def handle_listen(effect: EffectBase, task: TaskState, store: Store) -> FrameResult:
    from doeff.effects.writer import WriterListenEffect

    if not isinstance(effect, WriterListenEffect):
        raise TypeError(f"Expected WriterListenEffect, got {type(effect).__name__}")

    log_start = len(store.get("__log__", []))
    sub_program = effect.sub_program

    return ContinueProgram(
        program=sub_program,
        env=task.env,
        store=store,
        k=[ListenFrame(log_start)] + task.kontinuation,
    )


def handle_intercept(
    effect: EffectBase, task: TaskState, store: Store
) -> FrameResult:
    from doeff.effects.intercept import InterceptEffect

    if not isinstance(effect, InterceptEffect):
        raise TypeError(f"Expected InterceptEffect, got {type(effect).__name__}")

    sub_program = effect.program

    return ContinueProgram(
        program=sub_program,
        env=task.env,
        store=store,
        k=[InterceptFrame(effect.transforms)] + task.kontinuation,
    )


__all__ = [
    "handle_local",
    "handle_safe",
    "handle_listen",
    "handle_intercept",
]
