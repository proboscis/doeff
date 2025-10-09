"""
Kleisli arrow implementation for the doeff system.

This module contains the KleisliProgram class that enables automatic
unwrapping of Program arguments for natural composition.
"""

from __future__ import annotations

from collections.abc import Callable
import inspect
import types
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable as TypingCallable, Generic, ParamSpec, TypeVar

from doeff.program import Program, ProgramBase

P = ParamSpec("P")
T = TypeVar("T")
U = TypeVar("U")


@dataclass
class KleisliProgram(Generic[P, T]):
    """
    Thin wrapper around a callable representing a Kleisli arrow.

    The callable stored in ``func`` is expected to produce a Program when invoked
    with fully unwrapped arguments. Argument unwrapping now happens when the
    resulting KleisliProgramCall is executed, keeping this class as a lightweight
    data container.
    """

    func: Callable[P, Program[T]]

    def __post_init__(self) -> None:
        wrapped = getattr(self.func, "__wrapped__", self.func)
        self._metadata_source = wrapped

        signature = _safe_signature(wrapped) or _safe_signature(self.func)
        if signature is not None and not hasattr(self, "__signature__"):
            self.__signature__ = signature  # type: ignore[attr-defined]

        annotations = getattr(wrapped, "__annotations__", None)
        if annotations is None:
            annotations = getattr(self.func, "__annotations__", None)
        if annotations is not None and not hasattr(self, "__annotations__"):
            self.__annotations__ = dict(annotations)  # type: ignore[attr-defined]

        for attr in ("__name__", "__qualname__", "__doc__", "__module__"):
            if not hasattr(self, attr):
                value = getattr(wrapped, attr, getattr(self.func, attr, None))
                if value is not None:
                    setattr(self, attr, value)

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        return types.MethodType(self, instance)

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Program[T]:
        from doeff.program import KleisliProgramCall
        from doeff.utils import capture_creation_context

        return KleisliProgramCall.create_from_kleisli(
            kleisli=self,
            args=tuple(args),
            kwargs=dict(kwargs),
            function_name=getattr(self, "__name__", "<unknown>"),
            created_at=capture_creation_context(skip_frames=2),
        )

    def partial(
        self, /, *args: P.args, **kwargs: P.kwargs
    ) -> "PartiallyAppliedKleisliProgram[P, T]":
        return PartiallyAppliedKleisliProgram(self, args, kwargs)

    def and_then_k(
        self,
        binder: TypingCallable[[T], Program[U]],
    ) -> "KleisliProgram[P, U]":
        if not callable(binder):
            raise TypeError("binder must be callable returning a Program")

        @wraps(self.func)
        def composed(*args: P.args, **kwargs: P.kwargs) -> Program[U]:
            program = self(*args, **kwargs)
            if not isinstance(program, ProgramBase):
                raise TypeError("Kleisli program must return a Program")
            return program.and_then_k(binder)

        return KleisliProgram(composed)

    def __rshift__(
        self,
        binder: TypingCallable[[T], Program[U]],
    ) -> "KleisliProgram[P, U]":
        return self.and_then_k(binder)

    def fmap(
        self,
        mapper: TypingCallable[[T], U],
    ) -> "KleisliProgram[P, U]":
        if not callable(mapper):
            raise TypeError("mapper must be callable")

        @wraps(self.func)
        def mapped(*args: P.args, **kwargs: P.kwargs) -> Program[U]:
            program = self(*args, **kwargs)
            if not isinstance(program, ProgramBase):
                raise TypeError("Kleisli program must return a Program")
            return program.map(mapper)

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
        self._base = base
        self._pre_args = pre_args
        self._pre_kwargs = dict(pre_kwargs)

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


def _safe_signature(target: Any) -> inspect.Signature | None:
    try:
        return inspect.signature(target)
    except (TypeError, ValueError):
        return None


__all__ = ["KleisliProgram", "PartiallyAppliedKleisliProgram"]
