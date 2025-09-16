"""
Effect definitions for the doeff system.

This module provides the organized API for creating effects.
All effects are created as Effect instances with specific tags.

The effects are now organized into separate modules for better maintainability.
"""

# Import from individual modules
from .cache import (
    CacheGet,
    CacheLifecycle,
    CachePolicy,
    CachePut,
    CacheStorage,
    cache,
)
from .dep import Dep, dep
from .future import Await, Parallel, future
from .gather import Gather, GatherDict, gather
from .graph import Annotate, Step, graph
from .io import IO, Print
from .io import io as io_class
from .reader import Ask, Local, reader
from .result import Catch, Fail, Recover, Retry, result
from .state import Get, Modify, Put, state
from .writer import Listen, Log, Tell, writer

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
    "CachePolicy",
    "CacheLifecycle",
    "CacheStorage",
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
