"""Runtime helpers for composing handler maps with doeff_vm public APIs."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any

from doeff import Delegate, WithHandler

HandlerProtocol = Callable[[Any, Any], Any]
HandlerMap = Mapping[type, HandlerProtocol]


def with_handler_map(program: Any, handler_map: HandlerMap) -> Any:
    """Wrap a program with a typed effect-handler map."""
    typed_handlers = tuple(handler_map.items())

    def dispatch_handler(effect: Any, k):
        for effect_type, handler in typed_handlers:
            if isinstance(effect, effect_type):
                result = handler(effect, k)
                if inspect.isgenerator(result):
                    return (yield from result)
                return result
        yield Delegate()

    return WithHandler(handler=dispatch_handler, expr=program)


def merge_handler_maps(*handler_maps: HandlerMap) -> dict[type, HandlerProtocol]:
    """Merge handler maps left-to-right, with later maps overriding earlier ones."""
    merged: dict[type, HandlerProtocol] = {}
    for handler_map in handler_maps:
        merged.update(handler_map)
    return merged


def with_handler_maps(program: Any, *handler_maps: HandlerMap) -> Any:
    """Wrap a program with multiple handler maps using merge order semantics."""
    return with_handler_map(program, merge_handler_maps(*handler_maps))


__all__ = [
    "HandlerMap",
    "HandlerProtocol",
    "merge_handler_maps",
    "with_handler_map",
    "with_handler_maps",
]
