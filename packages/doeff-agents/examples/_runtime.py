"""Example runtime helpers for running programs on the doeff public API.

There is deliberately no public bundled default-handler stack (the old
``default_handlers()`` was removed; the bundled preset package was retired by
ADR-DOE-PRESET-001), so this example entry point composes its own — mirroring
``doeff.cli.run_services.default_interpreter``. Entry points equip
``slog_handler`` so ``yield slog(...)`` in the examples is visible on stderr
(ADR-DOE-CORE-EFFECTS-001 R4).
"""

from collections.abc import Sequence
from typing import Any

from doeff_core_effects.handlers import (
    await_handler,
    lazy_ask,
    listen_handler,
    slog_handler,
    state,
    try_handler,
    writer,
)
from doeff_core_effects.scheduler import scheduled

from doeff import handler as _program_handler
from doeff import run


async def run_program(
    program: Any,
    *,
    custom_handlers: Sequence[Any] = (),
) -> Any:
    """Run ``program`` under the standard stack plus ``custom_handlers``.

    ``custom_handlers`` (e.g. ``mock_agent_handlers()`` or
    ``agent_effectful_handlers()``) sit innermost, next to the program.
    Returns the program's return value; failures raise.
    """
    handlers = [
        lazy_ask(),
        state(),
        writer,
        try_handler,
        slog_handler,
        listen_handler,
        await_handler(),
        *custom_handlers,
    ]
    wrapped = program
    for h in reversed(handlers):
        wrapped = _program_handler(h)(wrapped)
    return run(scheduled(wrapped))
