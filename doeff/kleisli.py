"""
Kleisli arrow implementation for the doeff system.

This module contains the KleisliProgram class that enables automatic
unwrapping of Program arguments for natural composition.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable as TypingCallable, Generic, ParamSpec, TypeVar

from doeff.effects import Gather, GatherDict
from doeff.program import Program
from doeff.types import Effect

P = ParamSpec("P")
T = TypeVar("T")
U = TypeVar("U")


@dataclass(frozen=True)
class KleisliProgram(Generic[P, T]):
    """
    A Kleisli arrow that represents a function from parameters to Program[T].

    This class enables automatic unwrapping of Program arguments when called,
    allowing natural composition of Programs. When called with some arguments
    being Programs, it will automatically yield them to unwrap their values
    before passing them to the underlying function.

    Example:
        @do
        def add(x: int, y: int) -> Generator[..., ..., int]:
            return x + y

        # add is now KleisliProgram[(x: int, y: int), int]

        prog_x = Program.pure(5)
        result = add(x=prog_x, y=10)  # KleisliProgram unwraps prog_x automatically
    """

    func: Callable[P, Program[T]]

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Program[T]:
        """
        Call the Kleisli arrow, automatically unwrapping any Program arguments.

        This method uses the Gather effect to efficiently unwrap multiple Program
        arguments at once, then passes the unwrapped values to the underlying function.
        """


        @wraps(self.func)
        def unwrapping_generator() -> Generator[Effect | Program, Any, T]:
            # Collect Program arguments and their indices/keys
            program_args = []
            program_indices = []
            regular_args = []

            for i, arg in enumerate(args):
                if isinstance(arg, Program):
                    program_args.append(arg)
                    program_indices.append(i)
                    regular_args.append(None)  # Placeholder
                else:
                    regular_args.append(arg)

            program_kwargs = {}
            regular_kwargs = {}

            for key, value in kwargs.items():
                if isinstance(value, Program):
                    program_kwargs[key] = value
                else:
                    regular_kwargs[key] = value

            # If there are Program arguments, unwrap them
            if program_args or program_kwargs:
                # Unwrap positional Program arguments
                if program_args:
                    unwrapped_args = yield Gather(*program_args)
                    # Place unwrapped values back
                    for idx, unwrapped_value in zip(program_indices, unwrapped_args, strict=False):
                        regular_args[idx] = unwrapped_value

                # Unwrap keyword Program arguments
                if program_kwargs:
                    unwrapped_kwargs = yield GatherDict(program_kwargs)
                    regular_kwargs.update(unwrapped_kwargs)

            # Call the underlying function with unwrapped arguments
            result_program = self.func(*regular_args, **regular_kwargs)

            # If the function returns a Program, yield it to run it
            if isinstance(result_program, Program):
                result = yield result_program
                return result
            else:
                # Function returned a direct value
                return result_program

        return Program(unwrapping_generator)

    def partial(
        self, /, *args: P.args, **kwargs: P.kwargs
    ) -> "PartiallyAppliedKleisliProgram[P, T]":
        """Partially apply positional/keyword arguments to this Kleisli program."""

        return PartiallyAppliedKleisliProgram(self, args, kwargs)

    def and_then_k(
        self,
        binder: TypingCallable[[T], Program[U]],
    ) -> "KleisliProgram[P, U]":
        """Compose with ``binder`` that turns this program's result into a Program.

        The returned KleisliProgram keeps the same parameter signature as ``self``
        and, when executed, will run ``self`` first, feed its result into
        ``binder``, then execute the resulting Program.
        """

        if not callable(binder):
            raise TypeError("binder must be callable returning a Program")

        @wraps(self.func)
        def composed(*args: P.args, **kwargs: P.kwargs) -> Program[U]:

            def generator() -> Generator[Effect | Program, Any, U]:
                initial_value = yield self(*args, **kwargs)
                next_step = binder(initial_value)
                if not isinstance(next_step, Program):
                    raise TypeError(
                        "binder must return a Program; got "
                        f"{type(next_step).__name__}"
                    )
                result = yield next_step
                return result

            return Program(generator)

        return KleisliProgram(composed)

    def __rshift__(
        self,
        binder: TypingCallable[[T], Program[U]],
    ) -> "KleisliProgram[P, U]":
        """Alias for :meth:`and_then_k`, enabling ``program >> binder``."""

        return self.and_then_k(binder)

    def fmap(
        self,
        mapper: TypingCallable[[T], U],
    ) -> "KleisliProgram[P, U]":
        """Map ``mapper`` over the result produced by this Kleisli program."""

        if not callable(mapper):
            raise TypeError("mapper must be callable")

        @wraps(self.func)
        def mapped(*args: P.args, **kwargs: P.kwargs) -> Program[U]:

            def generator() -> Generator[Effect | Program, Any, U]:
                value = yield self(*args, **kwargs)
                return mapper(value)

            return Program(generator)

        return KleisliProgram(mapped)


class PartiallyAppliedKleisliProgram(KleisliProgram[P, T]):
    """Lightweight wrapper returned by ``KleisliProgram.partial``."""

    _base: KleisliProgram[P, T]
    _pre_args: tuple[Any, ...]
    _pre_kwargs: dict[str, Any]

    def __init__(
        self,
        base: KleisliProgram[P, T],
        pre_args: tuple[Any, ...],
        pre_kwargs: dict[str, Any],
    ) -> None:
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_pre_args", pre_args)
        object.__setattr__(self, "_pre_kwargs", dict(pre_kwargs))

    @property
    def func(self) -> Callable[P, Program[T]]:  # type: ignore[override]
        return self._base.func

    def __call__(self, *args: Any, **kwargs: Any) -> Program[T]:
        merged_args = self._pre_args + args
        merged_kwargs = {**self._pre_kwargs, **kwargs}
        return self._base(*merged_args, **merged_kwargs)

    def partial(
        self, /, *args: Any, **kwargs: Any
    ) -> "PartiallyAppliedKleisliProgram[P, T]":
        merged_args = self._pre_args + args
        merged_kwargs = {**self._pre_kwargs, **kwargs}
        return PartiallyAppliedKleisliProgram(self._base, merged_args, merged_kwargs)


__all__ = ["KleisliProgram", "PartiallyAppliedKleisliProgram"]
