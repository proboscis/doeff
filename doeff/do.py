"""
The do decorator for the doeff system.

This module provides the @do decorator that converts generator functions
into KleisliPrograms, enabling do-notation for monadic computations.
"""

from __future__ import annotations

from typing import Any, Callable, ParamSpec, TypeVar, Union, Generator

from doeff.types import Effect, EffectGenerator
from doeff.program import Program
from doeff.kleisli import KleisliProgram

P = ParamSpec("P")
T = TypeVar("T")


def do(
    func: Callable[P, EffectGenerator[T]],
) -> KleisliProgram[P, T]:
    """
    Decorator that converts a generator function into a KleisliProgram.

    ARCHITECTURAL SIGNIFICANCE:
    This decorator is the bridge between Python's generator syntax and our
    monadic do-notation. It transforms a generator function into a KleisliProgram,
    which is a Kleisli arrow that enables:
    - Clean syntax similar to Haskell's do-notation
    - Automatic unwrapping of Program arguments
    - Natural composition of Programs
    - Reusable programs (generators are single-use)
    - Deferred execution until run with an engine

    PYTHON LIMITATION WORKAROUND:
    Python generators execute immediately until first yield, but we need
    lazy evaluation. The KleisliProgram wrapper delays generator creation until
    execution time, achieving the laziness we need.
    
    CRITICAL ERROR HANDLING WARNING:
    DO NOT use try/except blocks inside @do functions with yield statements!
    
    Due to Python's generator protocol, exceptions don't propagate normally
    when yielding effects. A try/except block around a yield will NOT catch
    exceptions from the yielded effect. Instead, use effect-based error handling:
    
    WRONG (will not work as expected):
        @do
        def my_program():
            try:
                value = yield some_effect()
                return value
            except Exception as e:
                # This will NEVER catch exceptions from some_effect()!
                return default_value
    
    CORRECT (use Catch, Recover, or Retry effects):
        @do
        def my_program():
            # Option 1: Recover with fallback value
            value = yield Recover(some_effect(), fallback=default_value)
            
            # Option 2: Catch and handle error
            value = yield Catch(some_effect(), lambda e: default_value)
            
            # Option 3: Retry on failure
            value = yield Retry(some_effect(), max_attempts=3)
            
            return value
    
    The effect system provides these error handling effects:
    - Catch: Try a program and handle errors with a function
    - Recover: Try a program and use a fallback value on error
    - Retry: Retry a program multiple times on failure

    TYPE SIGNATURE CHANGE:
    @do changes: (args) -> EffectGenerator[T]
    into:        KleisliProgram[P, T] where P is the parameter spec
    This preserves type information and enables automatic Program unwrapping.

    Usage:
        @do
        def my_program(x: int) -> EffectGenerator[str]:
            config = yield ask("config")
            result = yield await_(process(x))
            yield log(f"Processed {x}")
            return f"Result: {result}"

        # my_program is now KleisliProgram[(x: int), str]
        # Can be called with regular or Program arguments
        result1 = my_program(42)  # Returns Program[str]
        result2 = my_program(x=Program.pure(42))  # Also returns Program[str]

    Args:
        func: A generator function that yields Effects/Programs and returns T

    Returns:
        KleisliProgram that wraps the generator function with automatic
        Program argument unwrapping.
    """

    def create_program(*args: P.args, **kwargs: P.kwargs) -> Program[T]:
        """Create a Program from the generator function."""

        def generator_wrapper() -> Generator[Union[Effect, Program], Any, T]:
            # Call the original generator function
            gen_or_value = func(*args, **kwargs)
            
            # Check if it's a generator or a direct value
            if not hasattr(gen_or_value, '__next__'):
                # Not a generator, must be a direct return value
                # Create a generator that immediately returns
                return gen_or_value
                if False:
                    yield  # Make this a generator function
            
            # It's a generator, pass through the generator protocol
            gen = gen_or_value
            try:
                current = next(gen)
                while True:
                    value = yield current
                    current = gen.send(value)
            except StopIteration as e:
                return e.value

        return Program(generator_wrapper)

    # Return a KleisliProgram that creates Programs lazily
    return KleisliProgram(create_program)


__all__ = ["do"]