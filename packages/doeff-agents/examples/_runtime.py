"""Example runtime helpers for running programs on doeff_vm public API."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from typing import Any

from doeff import Delegate, WithHandler, async_run, default_handlers

ProtocolHandler = Callable[[Any, Any], Any]
HandlerMap = dict[type, ProtocolHandler]


def _wrap_with_handler_map(program: Any, handler_map: HandlerMap) -> Any:
    wrapped = program

    for effect_type, handler in reversed(list(handler_map.items())):

        def typed_handler(effect, k, _effect_type=effect_type, _handler=handler):
            if isinstance(effect, _effect_type):
                result = _handler(effect, k)
                if inspect.isgenerator(result):
                    return (yield from result)
                return result
            yield Delegate()

        wrapped = WithHandler(handler=typed_handler, expr=wrapped)

    return wrapped


async def run_program(
    program: Any,
    *,
    handler_maps: Sequence[HandlerMap] = (),
    custom_handlers: Sequence[ProtocolHandler] = (),
    store: dict[str, Any] | None = None,
    env: dict[str, Any] | None = None,
) -> Any:
    """Run a program with handler-map wrappers plus protocol handlers."""
    wrapped = program
    for handler_map in handler_maps:
        wrapped = _wrap_with_handler_map(wrapped, handler_map)

    return await async_run(
        wrapped,
        handlers=[*custom_handlers, *default_handlers()],
        env=env,
        store=store,
    )
