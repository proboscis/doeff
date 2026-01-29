"""
Advanced patterns and best practices for doeff markers.

This file demonstrates various patterns and conventions for using
doeff marker comments effectively in your codebase.
"""

import functools
from collections.abc import Callable
from typing import Any, TypeVar

from doeff import Effect, Program, do

# ============================================================================
# PATTERN 1: Generic Type Parameters with Markers
# ============================================================================

T = TypeVar("T")
R = TypeVar("R")


def generic_interpreter(  # doeff: interpreter
    program: Program[T]
) -> T:
    """
    Generic interpreter that preserves type information.
    The marker works with generic type parameters.
    """
    return program.run()


@do
def map_transform(  # doeff: transform
    program: Program[T],
    mapper: Callable[[T], R]
) -> Program[R]:
    """
    Transform that maps over program results.
    Generic transform with type preservation.
    """
    return program.map(mapper)


# ============================================================================
# PATTERN 2: Decorator Stacking with Markers
# ============================================================================

@functools.lru_cache(maxsize=128)
@do
def cached_kleisli(  # doeff: kleisli
    key: str
):
    """
    Kleisli function with caching decorator.
    Markers work with multiple decorators.
    """
    yield Effect("cache_lookup", key=key)
    return f"cached_{key}"


def with_logging(func):
    """Decorator that adds logging to functions."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        print(f"Calling {func.__name__}")
        result = func(*args, **kwargs)
        print(f"Result: {result}")
        return result
    return wrapper


@with_logging
def logged_interpreter(  # doeff: interpreter
    program: Program
):
    """
    Interpreter with custom logging decorator.
    The marker is preserved through decoration.
    """
    return program.run()


# ============================================================================
# PATTERN 3: Protocol/Interface Implementation with Markers
# ============================================================================

from typing import Protocol


class Interpreter(Protocol):
    """Protocol defining interpreter interface."""

    def interpret(self, program: Program) -> Any:
        """Interpret a program."""
        ...


class CustomInterpreter:
    """Concrete interpreter implementation."""

    def interpret(  # doeff: interpreter
        self,
        program: Program
    ) -> Any:
        """
        Implementation of interpreter protocol.
        Marker on protocol method implementation.
        """
        return program.run_with_context({"interpreter": "custom"})


class BatchInterpreter:
    """Interpreter that handles multiple programs."""

    def interpret_batch(  # doeff: interpreter
        self,
        programs: list[Program]
    ) -> list[Any]:
        """
        Batch interpreter for multiple programs.
        Marker indicates this is an interpreter variant.
        """
        return [p.run() for p in programs]


# ============================================================================
# PATTERN 4: Conditional and Dynamic Markers
# ============================================================================

def conditional_interpreter(
    program: Program,
    mode: str = "default"
):  # doeff: interpreter
    """
    Interpreter with conditional behavior.
    Single marker despite multiple execution paths.
    """
    if mode == "debug":
        print(f"Debug: executing {program}")
        return program.run_debug()
    if mode == "async":
        import asyncio
        return asyncio.run(program.async_run())
    return program.run()


def interpreter_factory(
    strategy: str
) -> Callable[[Program], Any]:  # doeff: interpreter
    """
    Factory that creates interpreters based on strategy.
    The factory itself is marked as interpreter-related.
    """
    strategies = {
        "fast": lambda p: p.run_fast(),
        "safe": lambda p: p.run_safe(),
        "debug": lambda p: p.run_debug()
    }
    return strategies.get(strategy, lambda p: p.run())


# ============================================================================
# PATTERN 5: Partial Application and Currying with Markers
# ============================================================================

def curried_interpreter(  # doeff: interpreter
    config: dict
) -> Callable[[Program], Any]:
    """
    Curried interpreter that returns a configured interpreter.
    The outer function is marked, indicating interpreter creation.
    """
    def inner(program: Program) -> Any:
        return program.run_with_config(config)
    return inner


@do
def partial_transform(  # doeff: transform
    optimization_level: int = 1
) -> Callable[[Program], Program]:
    """
    Partially applied transform function.
    Returns a transformer with fixed optimization level.
    """
    @do
    def apply(program: Program) -> Program:
        for _ in range(optimization_level):
            program = program.optimize()
        return program
    return apply


# ============================================================================
# PATTERN 6: Composition Patterns with Markers
# ============================================================================

def compose_interpreters(  # doeff: interpreter
    *interpreters: Callable[[Program], Any]
) -> Callable[[Program], list[Any]]:
    """
    Composes multiple interpreters to run in sequence.
    The composer itself is marked as interpreter-related.
    """
    def composed(program: Program) -> list[Any]:
        return [interp(program) for interp in interpreters]
    return composed


@do
def pipeline_transform(  # doeff: transform
    *transforms: Callable[[Program], Program]
) -> Callable[[Program], Program]:
    """
    Creates a pipeline of transformations.
    The pipeline builder is marked as a transform.
    """
    def pipeline(program: Program) -> Program:
        result = program
        for transform in transforms:
            result = transform(result)
        return result
    return pipeline


# ============================================================================
# PATTERN 7: Context Managers with Markers
# ============================================================================

class InterpreterContext:
    """Context manager for interpreter execution."""

    def __enter__(self):  # doeff: interpreter
        """Enter interpreter context."""
        print("Entering interpreter context")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit interpreter context."""
        print("Exiting interpreter context")

    def run(self, program: Program) -> Any:  # doeff: interpreter
        """Run program in context."""
        return program.run()


# ============================================================================
# PATTERN 8: Async/Await Patterns with Markers
# ============================================================================

async def async_interpreter(  # doeff: interpreter
    program: Program
) -> Any:
    """
    Async interpreter using async/await.
    Markers work with async functions.
    """
    result = await program.async_run()
    return result


@do
async def async_kleisli():  # doeff: kleisli
    """
    Async Kleisli function.
    Combines @do decorator with async and marker.
    """
    result = yield Effect("async_operation")
    await asyncio.sleep(0.1)
    return result


# ============================================================================
# PATTERN 9: Property-based Markers
# ============================================================================

class ProgramManager:
    """Manager class with property-based program access."""

    def __init__(self, program: Program):
        self._program = program

    @property
    def interpreter(self) -> Callable[[], Any]:  # doeff: interpreter
        """
        Property that returns an interpreter function.
        Properties can be marked too.
        """
        return lambda: self._program.run()

    @property
    @do
    def transformer(self) -> Program:  # doeff: transform
        """
        Property that returns transformed program.
        Combines property with @do and marker.
        """
        return self._program.optimize()


# ============================================================================
# PATTERN 10: Error Handling with Markers
# ============================================================================

def safe_interpreter(  # doeff: interpreter
    program: Program,
    default: Any = None
) -> Any:
    """
    Interpreter with error handling.
    Returns default value on failure.
    """
    try:
        return program.run()
    except Exception as e:
        print(f"Error in interpreter: {e}")
        return default


@do
def resilient_transform(  # doeff: transform
    program: Program,
    max_retries: int = 3
) -> Program:
    """
    Transform with retry logic.
    Handles failures gracefully.
    """
    for attempt in range(max_retries):
        try:
            return program.optimize()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            yield Effect("retry", attempt=attempt, error=str(e))
    return program


# ============================================================================
# USAGE AND TESTING
# ============================================================================

if __name__ == "__main__":
    import asyncio

    from doeff import Program

    # Test generic interpreter
    prog = Program.of(lambda: "test")
    result = generic_interpreter(prog)
    print(f"Generic interpreter: {result}")

    # Test decorator stacking
    cached_result = cached_kleisli("key1")
    print(f"Cached kleisli: {cached_result}")

    # Test protocol implementation
    custom = CustomInterpreter()
    custom_result = custom.interpret(prog)
    print(f"Custom interpreter: {custom_result}")

    # Test curried interpreter
    configured = curried_interpreter({"debug": True})
    configured_result = configured(prog)
    print(f"Configured interpreter: {configured_result}")

    # Test context manager
    with InterpreterContext() as ctx:
        ctx_result = ctx.run(prog)
        print(f"Context manager result: {ctx_result}")

    print("\nAdvanced marker patterns demonstration complete!")
