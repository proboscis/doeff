"""
Program class for the doeff system.

This module contains the Program wrapper class that represents a lazy computation.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from doeff.types import Effect

T = TypeVar("T")
U = TypeVar("U")


@dataclass(frozen=True)
class Program(Generic[T]):
    """
    A program that can be executed by the engine.

    This is just a container for a generator function that yields effects.
    The engine will call the generator_func to get a fresh generator each time.

    The type parameter T represents the return type of the program.
    """

    generator_func: Callable[[], Generator[Effect | Program, Any, T]]

    def __iter__(self):
        """Allow iteration by returning a fresh generator."""
        return self.generator_func()

    def __repr__(self) -> str:
        """Return a developer-friendly representation including the wrapped function."""

        func = inspect.unwrap(self.generator_func)
        try:
            func_repr = repr(func)
        except Exception:  # pragma: no cover - very defensive, shouldn't happen
            func_repr = object.__repr__(func)
        return f"Program({func_repr})"

    def map(self, f: Callable[[T], U]) -> Program[U]:
        """Map a function over the result of this program (functor map)."""

        def mapped_generator():
            # First run the original program to completion
            gen = self.generator_func()
            try:
                # Check if it's a real generator or just returns immediately
                try:
                    current = next(gen)
                    # It's a real generator, run it
                    while True:
                        value = yield current
                        current = gen.send(value)
                except StopIteration as e:
                    # Apply the function to the result
                    return f(e.value)
            except GeneratorExit:
                # Handle generator cleanup
                gen.close()
                raise

        return Program(mapped_generator)

    def flat_map(self, f: Callable[[T], Program[U]]) -> Program[U]:
        """Monadic bind operation."""

        def flat_mapped_generator():
            # Run the first program
            gen = self.generator_func()
            try:
                try:
                    current = next(gen)
                    while True:
                        value = yield current
                        current = gen.send(value)
                except StopIteration as e:
                    # Get result from first program
                    first_result = e.value
                    # Apply f to get the second program
                    second_program = f(first_result)
                    # Run the second program
                    second_gen = second_program.generator_func()
                    try:
                        current = next(second_gen)
                        while True:
                            value = yield current
                            current = second_gen.send(value)
                    except StopIteration as e2:
                        return e2.value
            except GeneratorExit:
                gen.close()
                raise

        return Program(flat_mapped_generator)

    def then(self, next_program: Program[U]) -> Program[U]:
        """Sequence this program with another, discarding this program's result."""
        return self.flat_map(lambda _: next_program)

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> Program[T]:
        """Return a Program that applies ``transform`` to every yielded effect."""

        def intercepted_generator():
            gen = self.generator_func()
            try:
                current = next(gen)
            except StopIteration as exc:
                return exc.value

            while True:
                if isinstance(current, Program):
                    current = current.intercept(transform)
                    value = yield current
                elif isinstance(current, Effect):
                    intercepted_effect = current.intercept(transform)
                    transformed = transform(intercepted_effect)
                    if isinstance(transformed, Program):
                        value = yield transformed.intercept(transform)
                    elif isinstance(transformed, Effect):
                        value = yield transformed.intercept(transform)
                    else:
                        value = yield transformed
                else:
                    value = yield current

                try:
                    current = gen.send(value)
                except StopIteration as exc:
                    return exc.value

        return Program(intercepted_generator)

    @staticmethod
    def pure(value: T) -> Program[T]:
        """Create a program that returns the given value (monadic return)."""

        def pure_generator():
            return value
            yield  # Make it a generator (unreachable)

        return Program(pure_generator)

    @staticmethod
    def of(value: T) -> Program[T]:
        """Alias for pure."""
        return Program.pure(value)

    @staticmethod
    def from_effect(effect: Effect) -> Program[Any]:
        """Create a program from a single effect."""

        def effect_generator():
            result = yield effect
            return result

        return Program(effect_generator)

    @staticmethod
    def sequence(programs: list[Program[T]]) -> Program[list[T]]:
        """Sequence a list of programs, collecting their results."""

        def sequence_generator():
            results = []
            for prog in programs:
                gen = prog.generator_func()
                try:
                    current = next(gen)
                    while True:
                        value = yield current
                        current = gen.send(value)
                except StopIteration as e:
                    results.append(e.value)
            return results

        return Program(sequence_generator)

    @staticmethod
    def traverse(items: list[T], f: Callable[[T], Program[U]]) -> Program[list[U]]:
        """Map a function returning Programs over a list and sequence the results."""
        programs = [f(item) for item in items]
        return Program.sequence(programs)


__all__ = ["Program"]
