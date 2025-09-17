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
    Gather,
    GatherDict,
    MemoGet,
    MemoPut,
    Get,
    Listen,
    Local,
    Log,
    Modify,
    Parallel,
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
    fail,
    get,
    io,
    listen,
    local,
    memo_get,
    memo_put,
    modify,
    parallel,
    print_,
    put,
    snapshot,
    step,
    tell,
)
from doeff.handlers import (
    FutureEffectHandler,
    GraphEffectHandler,
    IOEffectHandler,
    ListenResult,
    ReaderEffectHandler,
    ResultEffectHandler,
    StateEffectHandler,
    WriterEffectHandler,
)
from doeff.interpreter import ProgramInterpreter, force_eval
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
    # Interpreter
    "ProgramInterpreter",
    "force_eval",
    # Handlers
    "ReaderEffectHandler",
    "StateEffectHandler",
    "WriterEffectHandler",
    "FutureEffectHandler",
    "ResultEffectHandler",
    "IOEffectHandler",
    "GraphEffectHandler",
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
    "Parallel",
    "Fail",
    "Catch",
    "IO",
    "Print",
    "Snapshot",
    "Step",
    "Annotate",
    "Gather",
    "GatherDict",
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
    "parallel",
    "fail",
    "catch",
    "io",
    "print_",
    "memo_get",
    "memo_put",
    "step",
    "annotate",
]
