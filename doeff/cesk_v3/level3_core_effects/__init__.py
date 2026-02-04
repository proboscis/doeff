"""Level 3: Core Effects & Reference Handlers.

This module provides core effects and reference handler implementations
built on Level 2 primitives. These serve as both standard library and
reference implementations for users.

Core Effects (pure, closure-based handlers):
    - State: Get, Put, Modify (keyed mutable state)
    - Reader: Ask, Local (environment/config access)
    - Writer: Tell, Listen (logging, output accumulation)

Users can implement their own effects by:
    1. Subclassing EffectBase to define custom effects
    2. Implementing handlers using @do + Level 2 primitives
    3. Using core effects as reference implementations
"""

from doeff.cesk_v3.level3_core_effects.state import (
    Get,
    Modify,
    Put,
    StateGetEffect,
    StateModifyEffect,
    StatePutEffect,
    state_handler,
)
from doeff.cesk_v3.level3_core_effects.reader import (
    Ask,
    AskEffect,
    reader_handler,
)
from doeff.cesk_v3.level3_core_effects.writer import (
    Tell,
    WriterTellEffect,
    writer_handler,
)

__all__ = [
    "Get",
    "Put",
    "Modify",
    "StateGetEffect",
    "StatePutEffect",
    "StateModifyEffect",
    "state_handler",
    "Ask",
    "AskEffect",
    "reader_handler",
    "Tell",
    "WriterTellEffect",
    "writer_handler",
]
