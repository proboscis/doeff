"""State effects and handler for CESK v3.

State effects provide keyed mutable state within a computation:
- Get(key): Retrieve the value for a key (returns None if missing)
- Put(key, value): Store a value for a key (returns None)
- Modify(key, func): Apply func to current value and store result (returns new value)

Usage:
    from doeff.cesk_v3.level3_core_effects import Get, Put, Modify, state_handler
    from doeff.cesk_v3 import WithHandler, run
    from doeff.do import do

    @do
    def program():
        yield Put("counter", 0)
        count = yield Get("counter")
        yield Put("counter", count + 1)
        return (yield Get("counter"))

    result = run(WithHandler(state_handler({}), program()))
    # result == 1
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from doeff.cesk_v3.level2_algebraic_effects.frames import EffectBase
from doeff.cesk_v3.level2_algebraic_effects.primitives import Forward, Resume
from doeff.do import do
from doeff.program import Program


@dataclass(frozen=True)
class StateGetEffect(EffectBase):
    """Retrieves the state value for key and yields it.

    Returns None if key is not present in state.
    """

    key: str


@dataclass(frozen=True)
class StatePutEffect(EffectBase):
    """Updates the stored state for key and completes with None."""

    key: str
    value: Any


@dataclass(frozen=True)
class StateModifyEffect(EffectBase):
    """Applies func to the current state value and yields the updated value.

    If key is not present, func receives None as the current value.
    """

    key: str
    func: Callable[[Any], Any]


def Get(key: str) -> StateGetEffect:
    """Create a state get effect.

    Args:
        key: The key to retrieve from state.

    Returns:
        StateGetEffect that yields the value for key (or None if missing).
    """
    return StateGetEffect(key=key)


def Put(key: str, value: Any) -> StatePutEffect:
    """Create a state put effect.

    Args:
        key: The key to store.
        value: The value to store.

    Returns:
        StatePutEffect that stores value and yields None.
    """
    return StatePutEffect(key=key, value=value)


def Modify(key: str, func: Callable[[Any], Any]) -> StateModifyEffect:
    """Create a state modify effect.

    Args:
        key: The key to modify.
        func: Function to apply to current value.

    Returns:
        StateModifyEffect that applies func and yields the new value.
    """
    return StateModifyEffect(key=key, func=func)


def state_handler(
    initial_state: dict[str, Any] | None = None,
) -> Callable[[EffectBase], Program[Any]]:
    """Create a state handler with initial state.

    The handler uses closure-captured state, so state is maintained across
    effect invocations within a single WithHandler scope.

    Args:
        initial_state: Initial state dictionary. Defaults to empty dict.

    Returns:
        Handler function compatible with WithHandler.

    Example:
        @do
        def program():
            yield Put("x", 10)
            return (yield Get("x"))

        result = run(WithHandler(state_handler({"x": 0}), program()))
        # result == 10
    """
    state: dict[str, Any] = dict(initial_state) if initial_state else {}

    @do
    def handler(effect: EffectBase) -> Program[Any]:
        if isinstance(effect, StateGetEffect):
            return (yield Resume(state.get(effect.key)))
        if isinstance(effect, StatePutEffect):
            state[effect.key] = effect.value
            return (yield Resume(None))
        if isinstance(effect, StateModifyEffect):
            old_value = state.get(effect.key)
            new_value = effect.func(old_value)
            state[effect.key] = new_value
            return (yield Resume(new_value))
        return (yield Forward(effect))

    return handler


__all__ = [
    "Get",
    "Modify",
    "Put",
    "StateGetEffect",
    "StateModifyEffect",
    "StatePutEffect",
    "state_handler",
]
