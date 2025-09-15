"""
The do decorator for the doeff system.

This module provides the @do decorator that converts generator functions
into KleisliPrograms, enabling do-notation for monadic computations.
"""

from __future__ import annotations

from typing import Any, Callable, Generator, ParamSpec, TypeVar, Union

from doeff.types import Effect
from doeff.program import Program
from doeff.kleisli import KleisliProgram

P = ParamSpec("P")
T = TypeVar("T")


def do(
    func: Callable[P, Generator[Union[Effect, Program], Any, T]],
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

    TYPE SIGNATURE CHANGE:
    @do changes: (args) -> Generator[Union[Effect, Program], Any, T]
    into:        KleisliProgram[P, T] where P is the parameter spec
    This preserves type information and enables automatic Program unwrapping.

    Usage:
        @do
        def my_program(x: int) -> Generator[Union[Effect, Program], Any, str]:
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