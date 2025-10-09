"""
The do decorator for the doeff system.

This module provides the @do decorator that converts generator functions
into KleisliPrograms, enabling do-notation for monadic computations.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Generator
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from doeff.kleisli import KleisliProgram
from doeff.program import Program
from doeff.types import Effect, EffectGenerator

P = ParamSpec("P")
T = TypeVar("T")


class DoYieldFunction(KleisliProgram[P, T]):
    """Specialised KleisliProgram for generator-based @do functions."""

    def __init__(self, func: Callable[P, EffectGenerator[T]]) -> None:
        @wraps(func)
        def generator_wrapper(
            *args: P.args, **kwargs: P.kwargs
        ) -> Generator[Effect | Program, Any, T]:
            gen_or_value = func(*args, **kwargs)
            if not inspect.isgenerator(gen_or_value):
                return gen_or_value

            gen = gen_or_value
            try:
                current = next(gen)
            except StopIteration as stop_exc:
                return stop_exc.value

            while True:
                sent_value = yield current
                try:
                    current = gen.send(sent_value)
                except StopIteration as stop_exc:
                    return stop_exc.value

        super().__init__(generator_wrapper)
        self.original_func = func

        for attr in ("__doc__", "__module__", "__name__", "__qualname__", "__annotations__"):
            value = getattr(func, attr, None)
            if value is not None:
                setattr(self, attr, value)

        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            setattr(self, "__signature__", signature)

    @property
    def original_generator(self) -> Callable[P, EffectGenerator[T]]:
        """Expose the user-defined generator for downstream tooling."""

        return self.original_func


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

    return DoYieldFunction(func)


__all__ = ["do", "DoYieldFunction"]
