"""
Core module for the doeff system.

This module re-exports the main components for backwards compatibility
and convenience. All implementations have been moved to their respective modules.
"""

from doeff.do import do
from doeff.effects import (
    IO,
    Annotate,
    # Capitalized aliases
    Ask,
    Await,
    Catch,
    Dep,
    Fail,
    Safe,
    Gather,
    MemoGet,
    MemoPut,
    Get,
    Listen,
    Local,
    Log,
    Modify,
    Print,
    Put,
    Snapshot,
    Step,
    Tell,
    annotate,
    # Lowercase compatibility
    ask,
    await_,
    catch,
    safe,
    fail,
    get,
    io,
    listen,
    local,
    memo_get,
    memo_put,
    modify,
    print_,
    put,
    snapshot,
    step,
    tell,
)
from doeff.types import ListenResult
from doeff.runtimes import AsyncioRuntime, SyncRuntime, SimulationRuntime
from doeff.kleisli import KleisliProgram
from doeff.program import Program
from doeff.types import Effect, ExecutionContext, RunResult

__all__ = [
    # Types
    "Effect",
    "ExecutionContext",
    "RunResult",
    "ListenResult",
    # Program types
    "Program",
    "KleisliProgram",
    # Decorator
    "do",
    # Runtimes (replacement for deprecated ProgramInterpreter)
    "AsyncioRuntime",
    "SyncRuntime",
    "SimulationRuntime",
    # Capitalized effect functions
    "Ask",
    "Local",
    "Get",
    "Put",
    "Modify",
    "Log",
    "Tell",
    "Listen",
    "Await",
    "Fail",
    "Safe",
    "Catch",
    "IO",
    "Print",
    "Snapshot",
    "Step",
    "Annotate",
    "Gather",
    "MemoGet",
    "MemoPut",
    "Dep",
    # Lowercase compatibility
    "ask",
    "local",
    "get",
    "put",
    "modify",
    "tell",
    "listen",
    "await_",
    "fail",
    "safe",
    "catch",
    "io",
    "print_",
    "memo_get",
    "memo_put",
    "step",
    "annotate",
]
