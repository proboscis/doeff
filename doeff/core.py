"""
Core module for the doeff system.

This module re-exports the main components for backwards compatibility
and convenience. All implementations have been moved to their respective modules.
"""

from doeff.types import Effect, ExecutionContext, RunResult
from doeff.program import Program
from doeff.kleisli import KleisliProgram
from doeff.do import do
from doeff.interpreter import ProgramInterpreter, force_eval
from doeff.handlers import (
    ReaderEffectHandler,
    StateEffectHandler,
    WriterEffectHandler,
    FutureEffectHandler,
    ResultEffectHandler,
    IOEffectHandler,
    GraphEffectHandler,
    ListenResult,
)
from doeff.effects import (
    Effects,
    # Capitalized aliases
    Ask,
    Local,
    Get,
    Put,
    Modify,
    Log,
    Tell,
    Listen,
    Await,
    Parallel,
    Fail,
    Catch,
    IO,
    Print,
    Step,
    Annotate,
    Gather,
    GatherDict,
    Dep,
    # Lowercase compatibility
    ask,
    local,
    get,
    put,
    modify,
    tell,
    listen,
    await_,
    parallel,
    fail,
    catch,
    io,
    print_,
    step,
    annotate,
)

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
    # Effects API
    "Effects",
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
    "Step",
    "Annotate",
    "Gather",
    "GatherDict",
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
    "step",
    "annotate",
]