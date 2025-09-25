"""
Program class for the doeff system.

This module contains the Program wrapper class that represents a lazy computation.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Generator, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from doeff.effects import gather, gather_dict, first_success_effect
from doeff.types import Effect, EffectBase, Maybe

if TYPE_CHECKING:
    from doeff.effects._program_types import ProgramLike

T = TypeVar("T")
U = TypeVar("U")
V = TypeVar("V")
S = TypeVar("S")


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
        """Return a Program that applies ``transform`` to every yielded effect.
        
        When transform returns a Program[Effect], the Program is yielded to get
        the resulting Effect, which is then yielded. This avoids infinite recursion
        while maintaining the Effect contract.
        """

        def intercepted_generator():
            gen = self.generator_func()
            transformed_cache: dict[int, Any] = {}
            try:
                current = next(gen)
            except StopIteration as exc:
                return exc.value

            while True:
                if isinstance(current, Program):
                    # Recursively intercept nested Programs
                    current = current.intercept(transform)
                    value = yield current
                elif isinstance(current, EffectBase):
                    effect_id = id(current)
                    cached = transformed_cache.get(effect_id)
                    if cached is not None:
                        transformed = cached
                    else:
                        transformed = transform(current)
                        transformed_cache[effect_id] = transformed
                    if isinstance(transformed, Program):
                        # Yield the Program to get the result (Effect or value)
                        # DO NOT recursively intercept to avoid infinite recursion
                        result = yield transformed
                        # If the result is an Effect, yield it
                        if isinstance(result, EffectBase):
                            value = yield result
                        else:
                            # Otherwise, use the result as the value
                            value = result
                    elif isinstance(transformed, EffectBase):
                        # Recursively intercept nested effects within the transformed effect
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
    def lift(value: "Program[U] | U") -> Program[U]:
        """Return ``value`` unchanged if it is already a Program, else wrap it in ``Program.pure``."""

        if isinstance(value, Program):
            return value
        if isinstance(value, Effect):
            # Effect values are converted to single-step Programs
            return Program.from_effect(value)  # type: ignore[return-value]
        return Program.pure(value)

    @staticmethod
    def from_effect(effect: Effect) -> Program[Any]:
        """Create a program from a single effect."""

        def effect_generator():
            result = yield effect
            return result

        return Program(effect_generator)

    @staticmethod
    def from_program_like(program_like: Program[Any] | Effect) -> Program[Any]:
        """Normalize a value that should be either ``Program`` or ``Effect``."""

        if isinstance(program_like, Program):
            return program_like
        if isinstance(program_like, Effect):
            return Program.from_effect(program_like)
        raise TypeError(
            "Expected Program or Effect, got "
            f"{type(program_like).__name__}"
        )

    @staticmethod
    def first_success(*programs: "ProgramLike[T]") -> "Program[T]":
        """Return a Program that yields the first successful result from ``programs``."""

        if not programs:
            raise ValueError("Program.first_success requires at least one program")

        def first_success_generator():
            effect = first_success_effect(*programs)
            value = yield effect
            return value

        return Program(first_success_generator)

    @staticmethod
    def first_some(*programs: "ProgramLike[S]") -> "Program[Maybe[S]]":
        """Return the first ``Some`` result from the provided programs."""

        if not programs:
            raise ValueError("Program.first_some requires at least one program")

        def first_some_generator():
            for candidate in programs:
                normalized = Program.from_program_like(candidate)
                value = yield normalized

                maybe = value if isinstance(value, Maybe) else Maybe.from_optional(value)

                if maybe.is_some():
                    return maybe

            return Maybe.from_optional(None)

        return Program(first_some_generator)

    @staticmethod
    def sequence(programs: list[Program[T]]) -> Program[list[T]]:
        """Sequence a list of programs, collecting their results in parallel where supported."""

        def sequence_generator():
            effect = gather(*programs)
            results = yield effect
            return list(results)

        return Program(sequence_generator)

    @staticmethod
    def traverse(items: list[T], f: Callable[[T], Program[U]]) -> Program[list[U]]:
        """Map a function returning Programs over a list and sequence the results."""
        programs = [f(item) for item in items]
        return Program.sequence(programs)

    @staticmethod
    def list(*values: Iterable["Program[U] | U"]) -> Program[list[U]]:
        """Construct a Program that resolves each element and returns a list."""

        programs = [Program.lift(value) for value in values]
        return Program.sequence(programs)

    @staticmethod
    def tuple(*values: Iterable["Program[U] | U"]) -> Program[tuple[U, ...]]:
        """Construct a Program that resolves each element and returns a tuple."""

        programs = [Program.lift(value) for value in values]
        return Program.sequence(programs).map(lambda items: tuple(items))

    @staticmethod
    def set(*values: Iterable["Program[U] | U"]) -> Program[set[U]]:
        """Construct a Program that resolves each element and returns a set."""

        programs = [Program.lift(value) for value in values]
        return Program.sequence(programs).map(lambda items: set(items))

    @staticmethod
    def dict(
        *mapping: Mapping[Any, "Program[V] | V"] | Iterable[tuple[Any, "Program[V] | V"]],
        **kwargs: "Program[V] | V",
    ) -> Program[dict[Any, V]]:
        """Construct a Program that resolves values and returns a dict.

        Mirrors ``dict`` semantics: positional arguments may be mappings or iterables of
        key/value pairs while keyword arguments provide additional items.
        """

        raw = dict(*mapping, **kwargs)

        def dict_generator():
            # gather_dict expects Programs or callables yielding Programs in mapping values
            program_map = {
                key: Program.lift(value)
                for key, value in raw.items()
            }
            effect = gather_dict(program_map)
            result = yield effect
            return dict(result)

        return Program(dict_generator)

    def __getattr__(self, item):
        return self.map(lambda x: getattr(x, item))

    def __getitem__(self, item):
        return self.map(lambda x: x[item])

    def __call__(self, *args, **kwargs):
        return self.map(lambda f: f(*args, **kwargs))




__all__ = ["Program"]
