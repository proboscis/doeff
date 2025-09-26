"""
Program class for the doeff system.

This module contains the Program wrapper class that represents a lazy computation.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Generator, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from doeff.types import Effect, EffectBase, Maybe
from doeff.effects import gather, gather_dict, first_success_effect

if TYPE_CHECKING:
    from doeff.effects._program_types import ProgramLike

T = TypeVar("T")
U = TypeVar("U")
V = TypeVar("V")


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

        return _InterceptedProgram.compose(self, (transform,))

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
    def first_some(*programs: "ProgramLike[V]") -> "Program[Maybe[V]]":
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




class _InterceptedProgram(Program[T]):
    """Program wrapper that composes multiple intercept transforms exactly once."""

    def __init__(
        self,
        base: Program[T],
        transforms: tuple[Callable[[Effect], Effect | Program], ...],
    ) -> None:
        def generator_func() -> Generator[Effect | Program, Any, T]:
            return self._intercept_generator(base, transforms)

        super().__init__(generator_func)
        object.__setattr__(self, "_base_program", base)
        object.__setattr__(self, "_transforms", transforms)

    @property
    def base_program(self) -> Program[T]:
        return self._base_program  # type: ignore[attr-defined]

    @property
    def transforms(self) -> tuple[Callable[[Effect], Effect | Program], ...]:
        return self._transforms  # type: ignore[attr-defined]

    @classmethod
    def compose(
        cls,
        program: Program[T],
        transforms: tuple[Callable[[Effect], Effect | Program], ...],
    ) -> Program[T]:
        if not transforms:
            return program

        if isinstance(program, cls):
            base = program.base_program
            combined = program.transforms + transforms
        else:
            base = program
            combined = transforms

        return cls(base, combined)

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> Program[T]:
        return self.compose(self.base_program, self.transforms + (transform,))

    @staticmethod
    def _intercept_generator(
        base: Program[T],
        transforms: tuple[Callable[[Effect], Effect | Program], ...],
    ) -> Generator[Effect | Program, Any, T]:
        gen = base.generator_func()
        try:
            current = next(gen)
        except StopIteration as exc:
            return exc.value

        kleisli_transform = _InterceptedProgram._compose_kleisli(transforms)

        while True:
            if isinstance(current, Program):
                current = _InterceptedProgram.compose(current, transforms)
                try:
                    current = gen.send((yield current))
                except StopIteration as exc:
                    return exc.value
                continue

            if isinstance(current, EffectBase):
                effect_program = kleisli_transform(current)
                final_effect = yield effect_program

                if not isinstance(final_effect, EffectBase):
                    raise TypeError(
                        "Intercept transform must resolve to an Effect, got "
                        f"{type(final_effect).__name__}"
                    )

                nested_effect = final_effect.intercept(
                    lambda eff: _InterceptedProgram._compose_kleisli(transforms)(eff)
                )
                result = yield nested_effect
                try:
                    current = gen.send(result)
                except StopIteration as exc:
                    return exc.value
                continue

            value = yield current
            try:
                current = gen.send(value)
            except StopIteration as exc:
                return exc.value

    @staticmethod
    def _compose_kleisli(
        transforms: tuple[Callable[[Effect], Effect | Program], ...]
    ) -> Callable[[EffectBase], Program[EffectBase]]:
        lifted = [_InterceptedProgram._lift_transform(transform) for transform in transforms]

        def combined(effect: EffectBase) -> Program[EffectBase]:
            program: Program[EffectBase] = Program.pure(effect)
            for lift in lifted:
                program = program.flat_map(lift)
            return program

        return combined

    @staticmethod
    def _lift_transform(
        transform: Callable[[Effect], Effect | Program]
    ) -> Callable[[EffectBase], Program[EffectBase]]:
        def lifted(effect: EffectBase) -> Program[EffectBase]:
            result = transform(effect)

            if isinstance(result, Program):
                return result.flat_map(_InterceptedProgram._ensure_effect_program)

            if isinstance(result, EffectBase):
                return Program.pure(result)

            raise TypeError(
                "Intercept transform must return Effect or Program yielding Effect, "
                f"got {type(result).__name__}"
            )

        return lifted


    @staticmethod
    def _ensure_effect_program(value: Any) -> Program[EffectBase]:
        if isinstance(value, EffectBase):
            return Program.pure(value)
        raise TypeError(
            "Intercept transform must resolve to an Effect, got "
            f"{type(value).__name__}"
        )
__all__ = ["Program"]
