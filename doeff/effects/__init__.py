"""
Effect definitions for the doeff system.

This module provides the organized API for creating effects.
All effects are created as Effect instances with specific tags.

The effects are now organized into separate modules for better maintainability.
"""

# Import from individual modules
from .reader import (
    reader, Ask, Local
)
from .state import (
    state, Get, Put, Modify
)
from .writer import (
    writer, Tell, Listen, Log
)
from .future import (
    future, Await, Parallel
)
from .result import (
    result, Fail, Catch, Recover, Retry
)
from .io import (
    io as io_class, IO, Print
)
from .graph import (
    graph, Step, Annotate
)
from .dep import (
    dep, Dep
)
from .gather import (
    gather, Gather, GatherDict
)
from .cache import (
    cache, CacheGet, CachePut
)


# ============================================
# Main Effects API Class
# ============================================

class Effects:
    """Organized effect creation API with grouped categories."""
    
    # Reference the imported classes directly
    reader = reader
    state = state
    writer = writer
    future = future
    result = result
    io = io_class
    graph = graph
    dep = dep
    gather = gather
    cache = cache


# ============================================
# Lowercase aliases for backward compatibility
# ============================================

# Create lowercase aliases only at the module level
ask = Ask
local = Local
get = Get
put = Put
modify = Modify
log = Log
tell = Tell
listen = Listen
await_ = Await
parallel = Parallel
fail = Fail
catch = Catch
recover = Recover
retry = Retry
io_func = IO  # Special case: io is a class, so use io_func
print_ = Print
step = Step
annotate = Annotate
cache_get = CacheGet
cache_put = CachePut

# Export io as function for backward compatibility
io = IO

# ============================================
# Exports
# ============================================

__all__ = [
    # Main API
    "Effects",
    # Uppercase functions
    "Ask",
    "Local",
    "Get",
    "Put",
    "Modify",
    "Log",
    "Tell",
    "Listen",
    "Await",
    "Parallel",
    "Fail",
    "Catch",
    "Recover",
    "Retry",
    "IO",
    "Print",
    "Step",
    "Annotate",
    "Dep",
    "Gather",
    "GatherDict",
    "CacheGet",
    "CachePut",
    # Lowercase aliases
    "ask",
    "local",
    "get",
    "put",
    "modify",
    "log",
    "tell",
    "listen",
    "await_",
    "parallel",
    "fail",
    "catch",
    "recover",
    "retry",
    "io",
    "print_",
    "step",
    "annotate",
    "cache_get",
    "cache_put",
]