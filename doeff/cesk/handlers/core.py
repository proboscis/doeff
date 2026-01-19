"""Core effect handlers for the unified CESK architecture."""

from __future__ import annotations

from typing import Any

from doeff.cesk.errors import MissingEnvKeyError
from doeff.cesk.frames import AskLazyFrame, ContinueProgram, ContinueValue, FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store
from doeff.effects.pure import PureEffect
from doeff.effects.reader import AskEffect
from doeff.effects.state import StateGetEffect, StateModifyEffect, StatePutEffect

# Sentinel for in-progress lazy Ask evaluations (cycle detection)
_ASK_IN_PROGRESS: Any = object()


class CircularAskError(Exception):
    """Raised when a circular dependency is detected in lazy Ask evaluation."""

    def __init__(self, key: object) -> None:
        self.key = key
        super().__init__(f"Circular dependency detected: Ask({key!r}) is already being evaluated")


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
        cache = store.get("__ask_lazy_cache__", {})

        # Check if result is already cached for this key
        if key in cache:
            cached_program, cached_value = cache[key]

            # Check for circular dependency (in-progress evaluation)
            if cached_value is _ASK_IN_PROGRESS:
                raise CircularAskError(key)

            # Cache hit: same program object means valid cached result
            # Different program object means Local override invalidated the cache
            if cached_program is value:
                return ContinueValue(
                    value=cached_value,
                    env=task_state.env,
                    store=store,
                    k=task_state.kontinuation,
                )
            # Cache invalidated by Local override - fall through to re-evaluate

        # Mark as in-progress before evaluation (cycle detection)
        new_cache = {**cache, key: (value, _ASK_IN_PROGRESS)}
        new_store = {**store, "__ask_lazy_cache__": new_cache}

        # Not cached or invalidated - evaluate the program and cache result
        return ContinueProgram(
            program=value,
            env=task_state.env,
            store=new_store,
            k=[AskLazyFrame(ask_key=key, program=value)] + task_state.kontinuation,
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
    "CircularAskError",
    "handle_ask",
    "handle_pure",
    "handle_state_get",
    "handle_state_modify",
    "handle_state_put",
]
