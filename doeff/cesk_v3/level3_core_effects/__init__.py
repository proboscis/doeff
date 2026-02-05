"""Level 3: Core Effects & Reference Handlers.

This module provides core effects and reference handler implementations
built on Level 2 primitives. These serve as both standard library and
reference implementations for users.

Core Effects (pure, closure-based handlers):
    - State: Get, Put, Modify (keyed mutable state)
    - Reader: Ask, Local (environment/config access)
    - Writer: Tell, Listen (output accumulation)
    - Cache: CacheGet, CachePut, CacheDelete, CacheExists (key-value caching)
    - Result: Safe (error handling with Ok/Err)
    - Asyncio Bridge: Await (Python awaitable integration)

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
    Local,
    LocalEffect,
    reader_handler,
)
from doeff.cesk_v3.level3_core_effects.writer import (
    Listen,
    Tell,
    WriterListenEffect,
    WriterTellEffect,
    writer_handler,
)
from doeff.cesk_v3.level3_core_effects.cache import (
    CACHE_MISS,
    CacheDelete,
    CacheDeleteEffect,
    CacheExists,
    CacheExistsEffect,
    CacheGet,
    CacheGetEffect,
    CachePut,
    CachePutEffect,
    cache_handler,
)
from doeff.cesk_v3.level3_core_effects.result import (
    Err,
    Ok,
    Result,
    Safe,
    SafeEffect,
    result_handler,
)
from doeff.cesk_v3.level3_core_effects.asyncio_bridge import (
    Await,
    AwaitEffect,
    python_async_syntax_escape_handler,
    sync_await_handler,
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
    "Local",
    "LocalEffect",
    "reader_handler",
    "Tell",
    "Listen",
    "WriterTellEffect",
    "WriterListenEffect",
    "writer_handler",
    "CACHE_MISS",
    "CacheGet",
    "CachePut",
    "CacheDelete",
    "CacheExists",
    "CacheGetEffect",
    "CachePutEffect",
    "CacheDeleteEffect",
    "CacheExistsEffect",
    "cache_handler",
    "Ok",
    "Err",
    "Result",
    "Safe",
    "SafeEffect",
    "result_handler",
    "Await",
    "AwaitEffect",
    "python_async_syntax_escape_handler",
    "sync_await_handler",
]
