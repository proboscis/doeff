"""Example runtime helpers for running programs on doeff_vm public API."""


from collections.abc import Sequence
from typing import Any

from doeff import async_run, default_handlers
from doeff import handler as _program_handler

async def run_program(
    program: Any,
    *,
    custom_handlers: Sequence[Any] = (),
    scoped_handlers: Sequence[Any] = (),
    store: dict[str, Any] | None = None,
    env: dict[str, Any] | None = None,
) -> Any:
    """Run a program with explicit ``WithHandler`` stacking plus runtime handlers."""
    wrapped = program
    for handler in reversed(tuple(scoped_handlers)):
        wrapped = _program_handler(handler)(wrapped)

    return await async_run(
        wrapped,
        handlers=[*custom_handlers, *default_handlers()],
        env=env,
        store=store,
    )
