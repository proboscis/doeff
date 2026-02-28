"""Example runtime helpers for running programs on doeff_vm public API."""


from collections.abc import Callable, Sequence
from typing import Any

from doeff import WithHandler, async_run, default_handlers

ProtocolHandler = Callable[[Any, Any], Any]


async def run_program(
    program: Any,
    *,
    custom_handlers: Sequence[ProtocolHandler] = (),
    scoped_handlers: Sequence[ProtocolHandler] = (),
    store: dict[str, Any] | None = None,
    env: dict[str, Any] | None = None,
) -> Any:
    """Run a program with explicit ``WithHandler`` stacking plus runtime handlers."""
    wrapped = program
    for handler in reversed(tuple(scoped_handlers)):
        wrapped = WithHandler(handler=handler, expr=wrapped)

    return await async_run(
        wrapped,
        handlers=[*custom_handlers, *default_handlers()],
        env=env,
        store=store,
    )
