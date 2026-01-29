"""Control flow effect handlers: local, safe, listen, intercept, tell."""

from __future__ import annotations

from doeff._vendor import FrozenDict
from doeff.cesk.frames import (
    ContinueProgram,
    ContinueValue,
    FrameResult,
    InterceptFrame,
    ListenFrame,
    LocalFrame,
    SafeFrame,
)
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store
from doeff.effects.intercept import InterceptEffect
from doeff.effects.reader import LocalEffect
from doeff.effects.result import ResultSafeEffect
from doeff.effects.writer import WriterListenEffect, WriterTellEffect


def handle_local(
    effect: LocalEffect,
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
    effect: ResultSafeEffect,
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
    effect: WriterListenEffect,
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
    effect: InterceptEffect,
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
    effect: WriterTellEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    current_log = store.get("__log__", [])
    message = effect.message
    new_log = list(current_log) + [message]
    new_store = {**store, "__log__": new_log}
    return ContinueValue(
        value=None,
        env=task_state.env,
        store=new_store,
        k=task_state.kontinuation,
    )


__all__ = [
    "handle_intercept",
    "handle_listen",
    "handle_local",
    "handle_safe",
    "handle_tell",
]
