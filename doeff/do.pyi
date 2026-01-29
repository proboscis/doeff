"""
Type stubs for the doeff @do decorator.

This stub provides accurate type information for pyright/mypy while the
runtime implementation uses generator tricks that confuse static analysis.
"""

from collections.abc import Callable
from typing import ParamSpec, TypeVar

from doeff.kleisli import KleisliProgram
from doeff.types import EffectGenerator

P = ParamSpec("P")
T = TypeVar("T")

class DoYieldFunction(KleisliProgram[P, T]):
    """
    Specialised KleisliProgram for generator-based @do functions.

    Type stub declares the contract: takes an EffectGenerator function,
    behaves as a KleisliProgram that produces Program[T] when called.
    """

    original_func: Callable[P, EffectGenerator[T]]

    def __init__(self, func: Callable[P, EffectGenerator[T]]) -> None: ...
    @property
    def original_generator(self) -> Callable[P, EffectGenerator[T]]: ...

def do(func: Callable[P, EffectGenerator[T]]) -> KleisliProgram[P, T]:
    """
    Decorator that converts a generator function into a KleisliProgram.

    Type signature:
        @do transforms: Callable[P, EffectGenerator[T]]
        into:           KleisliProgram[P, T]

    This enables do-notation where yielding effects returns their values,
    and the final return becomes the Program's result.
    """
    ...

__all__: list[str]
