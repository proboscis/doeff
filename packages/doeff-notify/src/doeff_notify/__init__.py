"""Notification effects and handlers for doeff."""
from importlib import import_module
from typing import TYPE_CHECKING

from ._effects import Acknowledge, Notify, NotifyThread
from ._handlers import (
    NotificationCapture,
    console_handler,
    log_handler,
    testing_handler,
)
from ._types import NotificationResult, Urgency

__all__ = [
    "Acknowledge",
    "NotificationCapture",
    "NotificationEffectBase",
    "NotificationResult",
    "Notify",
    "NotifyThread",
    "Urgency",
    "build_testing_handler",
    "console_handler",
    "log_handler",
    "testing_handler",
]


# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`
# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).
if TYPE_CHECKING:
    from doeff_notify._effects.notify import NotificationEffectBase as NotificationEffectBase
    from doeff_notify._handlers.testing import build_testing_handler as build_testing_handler

_LAZY_EXPORTS: dict[str, str] = {
    "NotificationEffectBase": "doeff_notify._effects.notify",
    "build_testing_handler": "doeff_notify._handlers.testing",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
