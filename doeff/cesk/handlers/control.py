"""Control flow effect handlers: Local, Safe, Listen, Intercept."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff._vendor import FrozenDict
from doeff.cesk.frames import InterceptFrame, ListenFrame, LocalFrame, SafeFrame
from doeff.cesk.state import ProgramControl, ReadyStatus, TaskState

if TYPE_CHECKING:
    from doeff.cesk.frames import Kontinuation
    from doeff.cesk.types import Environment, Store


def handle_local(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    new_env = env | FrozenDict(effect.env_update)
    return TaskState(
        control=ProgramControl(effect.sub_program),
        env=new_env,
        store=store,
        kontinuation=[LocalFrame(env)] + k,
        status=ReadyStatus(None),
    )


def handle_safe(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    return TaskState(
        control=ProgramControl(effect.sub_program),
        env=env,
        store=store,
        kontinuation=[SafeFrame(env)] + k,
        status=ReadyStatus(None),
    )


def handle_listen(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    log_start = len(store.get("__log__", []))
    return TaskState(
        control=ProgramControl(effect.sub_program),
        env=env,
        store=store,
        kontinuation=[ListenFrame(log_start)] + k,
        status=ReadyStatus(None),
    )


def handle_intercept(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    return TaskState(
        control=ProgramControl(effect.program),
        env=env,
        store=store,
        kontinuation=[InterceptFrame(effect.transforms)] + k,
        status=ReadyStatus(None),
    )


__all__ = [
    "handle_intercept",
    "handle_listen",
    "handle_local",
    "handle_safe",
]
