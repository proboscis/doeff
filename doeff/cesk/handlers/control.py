"""Control flow effect handlers: local, safe, listen, intercept, tell."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff._vendor import FrozenDict
from doeff.cesk.frames import (
    ContinueProgram,
    ContinueValue,
    InterceptFrame,
    ListenFrame,
    LocalFrame,
    SafeFrame,
)

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk.state import TaskState
    from doeff.cesk.types import Store
    from doeff.cesk.frames import FrameResult


def handle_local(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    new_env = task_state.env | FrozenDict(effect.env_update)
    return ContinueProgram(
        program=effect.sub_program,
        env=new_env,
        store=store,
        k=[LocalFrame(task_state.env)] + task_state.kontinuation,
    )


def handle_safe(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    return ContinueProgram(
        program=effect.sub_program,
        env=task_state.env,
        store=store,
        k=[SafeFrame(task_state.env)] + task_state.kontinuation,
    )


def handle_listen(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    log_start = len(store.get("__log__", []))
    return ContinueProgram(
        program=effect.sub_program,
        env=task_state.env,
        store=store,
        k=[ListenFrame(log_start)] + task_state.kontinuation,
    )


def handle_intercept(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    return ContinueProgram(
        program=effect.program,
        env=task_state.env,
        store=store,
        k=[InterceptFrame(effect.transforms)] + task_state.kontinuation,
    )


def handle_tell(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    current_log = store.get("__log__", [])
    messages = effect.messages
    if isinstance(messages, (list, tuple)):
        new_log = list(current_log) + list(messages)
    else:
        new_log = list(current_log) + [messages]
    new_store = {**store, "__log__": new_log}
    return ContinueValue(
        value=None,
        env=task_state.env,
        store=new_store,
        k=task_state.kontinuation,
    )


__all__ = [
    "handle_local",
    "handle_safe",
    "handle_listen",
    "handle_intercept",
    "handle_tell",
]
