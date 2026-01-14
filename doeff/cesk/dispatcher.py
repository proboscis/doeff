"""Scheduled effect dispatcher for the CESK machine."""

from __future__ import annotations

from doeff._types_internal import EffectBase
from doeff.cesk.types import Environment, Store
from doeff.runtime import HandlerResult, ScheduledEffectHandler, ScheduledHandlers


class HandlerRegistryError(Exception):
    """Raised when there's a conflict or invalid handler registration."""


class UnhandledEffectError(Exception):
    """Raised when no handler exists for an effect."""


class InterpreterInvariantError(Exception):
    """Raised when the interpreter reaches an invalid state."""


class ScheduledEffectDispatcher:
    """Dispatcher for looking up and invoking scheduled effect handlers."""

    def __init__(
        self,
        user_handlers: ScheduledHandlers | None = None,
        builtin_handlers: ScheduledHandlers | None = None,
    ):
        self._user = user_handlers or {}
        self._builtin = builtin_handlers or {}
        self._cache: dict[type[EffectBase], ScheduledEffectHandler | None] = {}

    def _lookup(self, effect_type: type[EffectBase]) -> ScheduledEffectHandler | None:
        if effect_type in self._cache:
            return self._cache[effect_type]

        if effect_type in self._user:
            handler = self._user[effect_type]
            self._cache[effect_type] = handler
            return handler

        if effect_type in self._builtin:
            handler = self._builtin[effect_type]
            self._cache[effect_type] = handler
            return handler

        for base in effect_type.__mro__[1:]:
            if base in self._user:
                handler = self._user[base]
                self._cache[effect_type] = handler
                return handler
            if base in self._builtin:
                handler = self._builtin[base]
                self._cache[effect_type] = handler
                return handler

        self._cache[effect_type] = None
        return None

    def has_handler(self, effect: EffectBase) -> bool:
        return self._lookup(type(effect)) is not None

    def dispatch(
        self,
        effect: EffectBase,
        env: Environment,
        store: Store,
    ) -> HandlerResult:
        handler = self._lookup(type(effect))
        if handler is None:
            raise UnhandledEffectError(f"No handler for {type(effect).__name__}")
        return handler(effect, env, store)


__all__ = [
    "HandlerRegistryError",
    "UnhandledEffectError",
    "InterpreterInvariantError",
    "ScheduledEffectDispatcher",
]
