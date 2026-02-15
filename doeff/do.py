"""
The do decorator for the doeff system.

This module provides the @do decorator that converts generator functions
into KleisliPrograms, enabling do-notation for monadic computations.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Generator
from functools import wraps
from typing import Any, ParamSpec, TypeVar, cast

from doeff.kleisli import KleisliProgram
from doeff.program import Program, _build_auto_unwrap_strategy
from doeff.types import Effect, EffectGenerator

P = ParamSpec("P")
T = TypeVar("T")

# Type alias for the internal generator wrapper signature.
# This is what generator_wrapper actually produces - a generator that yields
# effects/programs and returns T.
_GeneratorFunc = Callable[..., Generator[Effect | Program, Any, T]]


class DoYieldFunction(KleisliProgram[P, T]):
    """Specialised KleisliProgram for generator-based @do functions."""

    def __init__(self, func: Callable[P, EffectGenerator[T]]) -> None:
        @wraps(func)
        def generator_wrapper(
            *args: P.args, **kwargs: P.kwargs
        ) -> Generator[Effect | Program, Any, T]:
            gen_or_value = func(*args, **kwargs)
            if not inspect.isgenerator(gen_or_value):
                # Early return for non-generator callables (e.g., async functions
                # or plain functions mistakenly decorated). Pyright sees this as
                # returning T from a Generator function, but at runtime Python
                # allows returning before any yield. We cast to satisfy the checker.
                return cast(T, gen_or_value)  # type: ignore[return-value]

            gen = gen_or_value
            try:
                current = next(gen)
            except StopIteration as stop_exc:
                return stop_exc.value

            while True:
                try:
                    sent_value = yield current
                except GeneratorExit:
                    gen.close()
                    raise
                except BaseException as e:
                    try:
                        current = gen.throw(e)
                    except StopIteration as stop_exc:
                        return stop_exc.value
                    continue
                try:
                    current = gen.send(sent_value)
                except StopIteration as stop_exc:
                    return stop_exc.value

        # KleisliProgram.func expects Callable[P, Program[T]], but we pass a
        # generator function. Runtime call dispatch handles both Program and
        # generator returns from the execution kernel. Cast to satisfy pyright.
        super().__init__(cast(Callable[P, Program[T]], generator_wrapper))
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
            self.__signature__ = signature

        self.__doeff_do_decorated__ = True

        strategy = _build_auto_unwrap_strategy(self)
        object.__setattr__(self, "_auto_unwrap_strategy", strategy)

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

    ERROR HANDLING:
    Native Python try/except blocks work inside @do functions! Exceptions from
    yielded effects and sub-programs will be caught by surrounding try blocks:

    NATIVE TRY-EXCEPT (works as expected):
        @do
        def my_program():
            try:
                value = yield some_effect()
                return value
            except Exception as e:
                # This WILL catch exceptions from some_effect()!
                return default_value

    EFFECT-BASED ALTERNATIVES (for complex error handling):
        @do
        def my_program():
            # Use Safe to capture errors as Result values
            safe_result = yield Safe(some_effect())
            if safe_result.is_ok():
                value = safe_result.value
            else:
                value = default_value

            return value

    Both approaches work. Use native try-except for simple cases and the Safe effect
    for capturing errors as Result values that can be inspected and handled.

    TYPE SIGNATURE CHANGE:
    @do changes: (args) -> EffectGenerator[T]
    into:        KleisliProgram[P, T] where P is the parameter spec
    This preserves type information and enables automatic Program unwrapping.

    IMPORTANT — async def is NOT supported:
        @do requires a generator function (def with yield). Applying @do to an
        async def is always a bug — there is no "async kleisli" concept. async def
        returns a coroutine, not a generator, so the isgenerator() check fails and
        the coroutine is silently returned without executing the body.
        For async I/O, use a regular @do generator with yield Await(coroutine).

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

    if not callable(func):
        raise TypeError(f"@do expects a callable, got {type(func).__name__}")
    return DoYieldFunction(func)


__all__ = ["DoYieldFunction", "do"]
