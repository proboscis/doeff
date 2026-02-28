"""
doeff-pinjected - Pinjected integration for doeff effects system.

This module provides functions to convert Program[T] from doeff
into pinjected's Injected[T] and IProxy[T] types, enabling seamless
integration with pinjected's dependency injection framework.
"""


from typing import TYPE_CHECKING

from doeff_pinjected.effects import PinjectedEffectBase, PinjectedProvide, PinjectedResolve
from doeff_pinjected.handlers import MockPinjectedRuntime, mock_handlers, production_handlers

if TYPE_CHECKING:
    from doeff_pinjected.bridge import (
        program_to_injected,
        program_to_injected_result,
        program_to_iproxy,
        program_to_iproxy_result,
    )

__version__ = "0.1.0"
_BRIDGE_EXPORTS = {
    "program_to_injected",
    "program_to_injected_result",
    "program_to_iproxy",
    "program_to_iproxy_result",
}


def __getattr__(name: str):
    if name in _BRIDGE_EXPORTS:
        from doeff_pinjected import bridge as _bridge

        value = getattr(_bridge, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "MockPinjectedRuntime",
    "PinjectedEffectBase",
    "PinjectedProvide",
    "PinjectedResolve",
    "mock_handlers",
    "production_handlers",
    "program_to_injected",
    "program_to_injected_result",
    "program_to_iproxy",
    "program_to_iproxy_result",
]
