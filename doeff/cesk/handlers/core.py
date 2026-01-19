"""Core effect handlers for the unified CESK architecture."""

from __future__ import annotations

from doeff.cesk.errors import MissingEnvKeyError
from doeff.cesk.frames import AskLazyFrame, ContinueProgram, ContinueValue, FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store
from doeff.effects.pure import PureEffect
from doeff.effects.reader import AskEffect
from doeff.effects.state import StateGetEffect, StateModifyEffect, StatePutEffect


def handle_pure(
    effect: PureEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    return ContinueValue(
        value=effect.value,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def handle_ask(
    effect: AskEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    from doeff.program import ProgramBase

    key = effect.key
    if key not in task_state.env:
        raise MissingEnvKeyError(key)
    value = task_state.env[key]

    # Check if value is a Program (lazy evaluation per SPEC-EFF-001)
    if isinstance(value, ProgramBase):
        program_id = id(value)
        cache_key = (key, program_id)
        cache = store.get("__ask_lazy_cache__", {})

        # Check if result is already cached
        if cache_key in cache:
            cached_value = cache[cache_key]
            return ContinueValue(
                value=cached_value,
                env=task_state.env,
                store=store,
                k=task_state.kontinuation,
            )

        # Not cached - evaluate the program and cache result via AskLazyFrame
        return ContinueProgram(
            program=value,
            env=task_state.env,
            store=store,
            k=[AskLazyFrame(ask_key=key, program_id=program_id)] + task_state.kontinuation,
        )

    return ContinueValue(
        value=value,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def handle_state_get(
    effect: StateGetEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    key = effect.key
    if key not in store:
        raise KeyError(f"Missing state key: {key!r}")
    value = store[key]
    return ContinueValue(
        value=value,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def handle_state_put(
    effect: StatePutEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    new_store = {**store, effect.key: effect.value}
    return ContinueValue(
        value=None,
        env=task_state.env,
        store=new_store,
        k=task_state.kontinuation,
    )


def handle_state_modify(
    effect: StateModifyEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    old_value = store.get(effect.key)
    new_value = effect.func(old_value)
    new_store = {**store, effect.key: new_value}
    return ContinueValue(
        value=new_value,
        env=task_state.env,
        store=new_store,
        k=task_state.kontinuation,
    )


__all__ = [
    "handle_ask",
    "handle_pure",
    "handle_state_get",
    "handle_state_modify",
    "handle_state_put",
]
