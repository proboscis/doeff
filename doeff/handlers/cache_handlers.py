"""Compat: doeff.handlers.cache_handlers — re-exports from doeff_core_effects."""
from doeff_core_effects.cache_handlers import (
    cache_handler,
    sqlite_cache_handler,
    in_memory_cache_handler,
)
from doeff_core_effects.cache import cache as memo_rewriters  # compat alias
