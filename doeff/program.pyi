from __future__ import annotations

from collections.abc import Callable, Generator, Iterable, Mapping
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from doeff.effects._program_types import ProgramLike
from doeff.types import Effect, Maybe

T = TypeVar("T")
U = TypeVar("U")
V = TypeVar("V")

class ProgramBase(Generic[T]):
    def __class_getitem__(cls, item) -> Any: ...
    def __getattr__(self, name: str) -> Program[Any]: ...
    def __getitem__(self, key: Any) -> Program[Any]: ...
    def __call__(self, *args: Any, **kwargs: Any) -> Program[Any]: ...
    def map(self, f: Callable[[T], U]) -> Program[U]: ...
    def flat_map(self, f: Callable[[T], Program[U]]) -> Program[U]: ...
    def and_then_k(self, binder: Callable[[T], Program[U]]) -> Program[U]: ...
    def intercept(self, transform: Callable[[Effect], Effect | Program]) -> Program[T]: ...
    @staticmethod
    def pure(value: T) -> Program[T]: ...
    @staticmethod
    def of(value: T) -> Program[T]: ...
    @staticmethod
    def lift(value: Program[U] | U) -> Program[U]: ...
    @staticmethod
    def first_success(*programs: ProgramLike[T]) -> Program[T]: ...
    @staticmethod
    def first_some(*programs: ProgramLike[V]) -> Program[Maybe[V]]: ...
    @staticmethod
    def sequence(programs: list[Program[T]]) -> Program[list[T]]: ...
    @staticmethod
    def traverse(items: list[T], func: Callable[[T], Program[U]]) -> Program[list[U]]: ...
    @staticmethod
    def list(*values: Program[U] | U) -> Program[list[U]]: ...
    @staticmethod
    def tuple(*values: Program[U] | U) -> Program[tuple[U, ...]]: ...
    @staticmethod
    def set(*values: Program[U] | U) -> Program[set[U]]: ...
    @staticmethod
    def dict(
        *mapping: Mapping[Any, Program[V] | V] | Iterable[tuple[Any, Program[V] | V]],
        **kwargs: Program[V] | V,
    ) -> Program[dict[Any, V]]: ...

@runtime_checkable
class ProgramProtocol(Protocol[T]):
    def map(self, f: Callable[[T], U]) -> ProgramProtocol[U]: ...
    def flat_map(self, f: Callable[[T], ProgramProtocol[U]]) -> ProgramProtocol[U]: ...
    def intercept(
        self, transform: Callable[[Effect], Effect | ProgramProtocol]
    ) -> ProgramProtocol[T]: ...

class KleisliProgramCall(ProgramBase[T]):
    kleisli_source: Any
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    function_name: str
    created_at: Any
    auto_unwrap_strategy: Any | None
    execution_kernel: Callable[..., Generator[Effect | Program, Any, T]] | None

    def to_generator(self) -> Generator[Effect | Program, Any, T]: ...
    @classmethod
    def create_from_kleisli(
        cls,
        kleisli: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        function_name: str,
        created_at: Any = ...,
    ) -> KleisliProgramCall[T]: ...
    @classmethod
    def create_derived(
        cls,
        generator_func: Callable[..., Generator[Effect | Program, Any, T]],
        parent: KleisliProgramCall[Any],
        args: tuple[Any, ...] | None = ...,
        kwargs: dict[str, Any] | None = ...,
    ) -> KleisliProgramCall[T]: ...
    def map(self, f: Callable[[T], U]) -> KleisliProgramCall[U]: ...
    def flat_map(self, f: Callable[[T], ProgramProtocol[U]]) -> KleisliProgramCall[U]: ...

class GeneratorProgram(ProgramBase[T]):
    factory: Callable[[], Generator[Effect | Program, Any, T]]
    created_at: Any | None

    def to_generator(self) -> Generator[Effect | Program, Any, T]: ...

class _InterceptedProgram(ProgramBase[T]):
    base_program: Program[T]
    transforms: tuple[Callable[[Effect], Effect | Program], ...]

    def to_generator(self) -> Generator[Effect | Program, Any, T]: ...
    @classmethod
    def compose(
        cls,
        program: Program[T],
        transforms: tuple[Callable[[Effect], Effect | Program], ...],
    ) -> Program[T]: ...
    def intercept(self, transform: Callable[[Effect], Effect | Program]) -> Program[T]: ...

Program = ProgramBase

__all__ = ["GeneratorProgram", "KleisliProgramCall", "Program", "ProgramProtocol"]
