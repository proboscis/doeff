from __future__ import annotations

from collections.abc import Callable, Generator, Iterable, Mapping
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from doeff.effects._program_types import ProgramLike
from doeff.types import Effect, EffectBase, Maybe

T = TypeVar("T")
U = TypeVar("U")
V = TypeVar("V")

__all__ = ["Program", "ProgramProtocol", "KleisliProgramCall"]


class ProgramBase(Generic[T]):
    def __class_getitem__(cls, item) -> Any: ...

    @staticmethod
    def pure(value: T) -> Program[T]: ...

    @staticmethod
    def of(value: T) -> Program[T]: ...

    @staticmethod
    def lift(value: Program[U] | U) -> Program[U]: ...

    @staticmethod
    def first_success(*programs: ProgramLike[T]) -> KleisliProgramCall[T]: ...

    @staticmethod
    def first_some(*programs: ProgramLike[V]) -> KleisliProgramCall[Maybe[V]]: ...

    @staticmethod
    def sequence(programs: list[Program[T]]) -> KleisliProgramCall[list[T]]: ...

    @staticmethod
    def traverse(items: list[T], func: Callable[[T], Program[U]]) -> KleisliProgramCall[list[U]]: ...

    @staticmethod
    def list(*values: Iterable[Program[U] | U]) -> KleisliProgramCall[list[U]]: ...

    @staticmethod
    def tuple(*values: Iterable[Program[U] | U]) -> KleisliProgramCall[tuple[U, ...]]: ...

    @staticmethod
    def set(*values: Iterable[Program[U] | U]) -> KleisliProgramCall[set[U]]: ...

    @staticmethod
    def dict(
        *mapping: Mapping[Any, Program[V] | V] | Iterable[tuple[Any, Program[V] | V]],
        **kwargs: Program[V] | V,
    ) -> KleisliProgramCall[dict[Any, V]]: ...


@runtime_checkable
class ProgramProtocol(Protocol[T]):
    def map(self, f: Callable[[T], U]) -> ProgramProtocol[U]: ...
    def flat_map(self, f: Callable[[T], ProgramProtocol[U]]) -> ProgramProtocol[U]: ...
    def intercept(self, transform: Callable[[Effect], Effect | ProgramProtocol]) -> ProgramProtocol[T]: ...


class KleisliProgramCall(ProgramBase[T]):
    generator_func: Callable[..., Generator[Effect | Program, Any, T]]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    kleisli_source: Any | None
    function_name: str
    created_at: Any | None

    def to_generator(self) -> Generator[Effect | Program, Any, T]: ...

    @classmethod
    def create_from_kleisli(
        cls,
        kleisli: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        function_name: str,
        created_at: Any | None = ...,
    ) -> KleisliProgramCall[T]: ...

    @classmethod
    def create_anonymous(
        cls,
        generator_func: Callable[..., Generator[Effect | Program, Any, T]],
        args: tuple[Any, ...] = ...,
        kwargs: dict[str, Any] | None = ...,
    ) -> KleisliProgramCall[T]: ...

    @classmethod
    def create_derived(
        cls,
        generator_func: Callable[..., Generator[Effect | Program, Any, T]],
        parent: KleisliProgramCall[Any],
        args: tuple[Any, ...] | None = ...,
        kwargs: dict[str, Any] | None = ...,
    ) -> KleisliProgramCall[T]: ...


class _InterceptedProgram(KleisliProgramCall[T]):
    _base_program: Program[T]
    _transforms: tuple[Callable[[Effect], Effect | Program], ...]

    @property
    def base_program(self) -> Program[T]: ...

    @property
    def transforms(self) -> tuple[Callable[[Effect], Effect | Program], ...]: ...

    @classmethod
    def compose(
        cls,
        program: Program[T],
        transforms: tuple[Callable[[Effect], Effect | Program], ...],
    ) -> Program[T]: ...


Program = ProgramBase
