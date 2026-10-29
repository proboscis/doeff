"""Runtime helpers for explicit handler stacking with ``WithHandler``."""


from collections.abc import Sequence
from typing import Any

from doeff import handler as _program_handler


def with_handlers(program: Any, handlers: Sequence[Any]) -> Any:
    """Wrap ``program`` with handlers ordered from outer to inner."""
    wrapped = program
    for handler in reversed(tuple(handlers)):
        wrapped = _program_handler(handler)(wrapped)
    return wrapped


__all__ = ["with_handlers"]
