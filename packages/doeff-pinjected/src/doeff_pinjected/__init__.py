"""
doeff-pinjected - Pinjected integration for doeff effects system.

This module provides functions to convert Program[T] from doeff
into pinjected's Injected[T] and IProxy[T] types, enabling seamless
integration with pinjected's dependency injection framework.
"""

from doeff_pinjected.bridge import (
    program_to_injected,
    program_to_injected_result,
    program_to_iproxy,
    program_to_iproxy_result,
)

__version__ = "0.1.0"

__all__ = [
    "program_to_injected",
    "program_to_injected_result",
    "program_to_iproxy",
    "program_to_iproxy_result",
]
