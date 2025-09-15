"""
Kleisli arrow implementation for the doeff system.

This module contains the KleisliProgram class that enables automatic
unwrapping of Program arguments for natural composition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generator, Generic, ParamSpec, TypeVar, Union

from doeff.types import Effect, EffectGenerator
from doeff.program import Program
from doeff.effects import Gather, GatherDict

P = ParamSpec("P")
T = TypeVar("T")


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

    func: Callable[P, "Program[T]"]

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> "Program[T]":
        """
        Call the Kleisli arrow, automatically unwrapping any Program arguments.

        This method uses the Gather effect to efficiently unwrap multiple Program
        arguments at once, then passes the unwrapped values to the underlying function.
        """

        def unwrapping_generator() -> Generator[Union[Effect, "Program"], Any, T]:
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
                    for idx, unwrapped_value in zip(program_indices, unwrapped_args):
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


__all__ = ["KleisliProgram"]