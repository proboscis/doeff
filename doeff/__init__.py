"""
doeff - Do-notation and Effects system for Python.

A practical free monad implementation that prioritizes usability and Python idioms
over theoretical purity. Uses generators for do-notation and supports comprehensive
effects including Reader, State, Writer, Future, Result, and IO.

Example:
    >>> from doeff import do, Program, Put, Get, Log
    >>>
    >>> @do
    >>> def example_program():
    ...     yield Put("counter", 0)
    ...     yield Log("Starting computation")
    ...     count = yield Get("counter")
    ...     return count + 1
"""

# Core types
from doeff.core import (
    # Main types
    Program,
    ProgramInterpreter,
    ExecutionContext,
    RunResult,
    Effect,
    Effects,
    # Decorator
    do,
    # Kleisli arrow support
    KleisliProgram,
    # Effects - Reader
    Ask,
    Local,
    # Effects - State
    Get,
    Put,
    Modify,
    # Effects - Writer
    Log,
    Tell,
    Listen,
    ListenResult,
    # Effects - Future
    Await,
    Parallel,
    # Effects - Result
    Fail,
    Catch,
    # Effects - IO
    IO,
    Print,
    # Effects - Graph tracking
    Step,
    Annotate,
    # Effects - Dependency injection (pinjected compatible)
    Dep,
    # Effects - Gather for parallel Programs
    Gather,
    GatherDict,
    # Lowercase effect functions (aliases)
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

__version__ = "0.1.0"

__all__ = [
    "IO",
    "Annotate",
    "Ask",
    "Await",
    "Catch",
    "Dep",
    "Effect",
    "Effects",
    "ExecutionContext",
    "Fail",
    "Gather",
    "GatherDict",
    "Get",
    "KleisliProgram",
    "Listen",
    "ListenResult",
    "Local",
    "Log",
    "Modify",
    "Parallel",
    "Print",
    "Program",
    "ProgramInterpreter",
    "Put",
    "RunResult",
    "Step",
    "Tell",
    "annotate",
    "ask",
    "await_",
    "catch",
    "do",
    "fail",
    "get",
    "io",
    "listen",
    "local",
    "modify",
    "parallel",
    "print_",
    "put",
    "step",
    "tell",
]