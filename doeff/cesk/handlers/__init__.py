from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Protocol

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk.types import Store, Environment
    from doeff.cesk.actions import Action


@dataclass(frozen=True)
class HandlerContext:
    store: Store
    environment: Environment
    kontinuation: list[Any]


class HandlerResult(Protocol):
    pass


@dataclass(frozen=True)
class ResumeWith:
    value: Any


@dataclass(frozen=True)
class ResumeWithError:
    error: BaseException


@dataclass(frozen=True)
class PerformAction:
    action: Action


Handler = Callable[[EffectBase, HandlerContext], HandlerResult]


class HandlerRegistry:
    def __init__(self):
        self._handlers: dict[type, Handler] = {}
    
    def register(self, effect_type: type, handler: Handler) -> None:
        self._handlers[effect_type] = handler
    
    def get_handler(self, effect_type: type) -> Handler | None:
        return self._handlers.get(effect_type)
    
    def has_handler(self, effect_type: type) -> bool:
        return effect_type in self._handlers


_default_registry = HandlerRegistry()


def register_handler(effect_type: type):
    def decorator(handler: Handler) -> Handler:
        _default_registry.register(effect_type, handler)
        return handler
    return decorator


def get_default_registry() -> HandlerRegistry:
    return _default_registry


__all__ = [
    "HandlerContext",
    "HandlerResult",
    "ResumeWith",
    "ResumeWithError",
    "PerformAction",
    "Handler",
    "HandlerRegistry",
    "register_handler",
    "get_default_registry",
]
