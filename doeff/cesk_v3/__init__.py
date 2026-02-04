"""CESK v3: Layered Interpreter Architecture for Algebraic Effects.

This module implements the unified K architecture where all handler and
dispatch state lives in K (continuation stack). No separate handler stack.

Architecture:
    Level 0: Python (CPython) - executes bytecode, manages generators
    Level 1: Pure CESK Machine - generator stepping, ReturnFrame only
    Level 2: Algebraic Effects - WithHandlerFrame, DispatchingFrame, control primitives

See SPEC-CESK-006 for detailed architecture documentation.
"""

from doeff.cesk_v3.errors import UnhandledEffectError
from doeff.cesk_v3.run import run

__all__ = [
    "UnhandledEffectError",
    "run",
]
