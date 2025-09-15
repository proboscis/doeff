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

# Core types from modular structure
from doeff.types import (
    ExecutionContext,
    RunResult,
    Effect,
    ListenResult,
    # Vendored types
    Ok,
    Err,
    Result,
    TraceError,
    trace_err,
    WNode,
    WStep,
    WGraph,
    FrozenDict,
)

from doeff.effects import (
    Effects,
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

# Import from new modular structure
from doeff.program import Program
from doeff.interpreter import ProgramInterpreter
from doeff.kleisli import KleisliProgram
from doeff.do import do

__version__ = "0.1.0"

__all__ = [
    # Core types
    "Effect",
    "Program",
    "ExecutionContext",
    "RunResult",
    "ListenResult",
    # Vendored types
    "Ok",
    "Err",
    "Result",
    "TraceError",
    "trace_err",
    "WNode",
    "WStep",
    "WGraph",
    "FrozenDict",
    # Core classes
    "ProgramInterpreter",
    "KleisliProgram",
    # Decorator
    "do",
    # Effects API
    "Effects",
    # Effects - Uppercase
    "IO",
    "Annotate",
    "Ask",
    "Await",
    "Catch",
    "Dep",
    "Fail",
    "Gather",
    "GatherDict",
    "Get",
    "Listen",
    "Local",
    "Log",
    "Modify",
    "Parallel",
    "Print",
    "Put",
    "Step",
    "Tell",
    # Effects - lowercase
    "annotate",
    "ask",
    "await_",
    "catch",
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