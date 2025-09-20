"""
Kleisli arrow implementation for the doeff system.

This module contains the KleisliProgram class that enables automatic
unwrapping of Program arguments for natural composition.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
import inspect
import types
from dataclasses import dataclass
from functools import wraps
from typing import (
    Annotated,
    Any,
    Callable as TypingCallable,
    ForwardRef,
    Generic,
    ParamSpec,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from doeff.effects import Gather, GatherDict
from doeff.program import Program
from doeff.types import Effect

P = ParamSpec("P")
T = TypeVar("T")
U = TypeVar("U")


class _AutoUnwrapStrategy:
    """Describe which arguments should be auto-unwrapped."""

    __slots__ = ("positional", "var_positional", "keyword", "var_keyword")

    def __init__(self) -> None:
        self.positional: list[bool] = []
        self.var_positional: bool | None = None
        self.keyword: dict[str, bool] = {}
        self.var_keyword: bool | None = None

    def should_unwrap_positional(self, index: int) -> bool:
        if index < len(self.positional):
            return self.positional[index]
        if self.var_positional is not None:
            return self.var_positional
        return True

    def should_unwrap_keyword(self, name: str) -> bool:
        if name in self.keyword:
            return self.keyword[name]
        if self.var_keyword is not None:
            return self.var_keyword
        return True


def _string_annotation_is_program(annotation_text: str) -> bool:
    if not annotation_text:
        return False
    stripped = annotation_text.strip()
    if not stripped:
        return False
    if "|" in stripped:
        return any(_string_annotation_is_program(part) for part in stripped.split("|"))
    if stripped.startswith("Optional[") and stripped.endswith("]"):
        return _string_annotation_is_program(stripped[9:-1])
    if stripped.startswith("typing.Optional[") and stripped.endswith("]"):
        return _string_annotation_is_program(stripped[len("typing.Optional["):-1])
    if stripped.startswith("Annotated[") and stripped.endswith("]"):
        inner = stripped[len("Annotated["):-1]
        first_part = inner.split(",", 1)[0]
        return _string_annotation_is_program(first_part)
    normalized = stripped.replace(" ", "")
    return (
        normalized == "Program"
        or normalized.startswith("Program[")
        or normalized.startswith("doeff.program.Program")
    )


def _annotation_is_program(annotation: Any) -> bool:
    if annotation is inspect._empty:
        return False
    if annotation is Program:
        return True
    if isinstance(annotation, ForwardRef):
        return _string_annotation_is_program(annotation.__forward_arg__)
    if isinstance(annotation, str):
        return _string_annotation_is_program(annotation)
    origin = get_origin(annotation)
    if origin is Program:
        return True
    if origin is Annotated:
        args = get_args(annotation)
        if args:
            return _annotation_is_program(args[0])
        return False
    union_type = getattr(types, 'UnionType', None)
    if origin is Union or (union_type is not None and origin is union_type):
        return any(_annotation_is_program(arg) for arg in get_args(annotation))
    return False


def _safe_get_type_hints(target: Any) -> dict[str, Any]:
    if target is None:
        return {}
    try:
        return get_type_hints(target, include_extras=True)
    except Exception:
        annotations = getattr(target, "__annotations__", None)
        return dict(annotations) if annotations else {}


def _safe_signature(target: Any) -> inspect.Signature | None:
    try:
        return inspect.signature(target)
    except (TypeError, ValueError):
        return None


def _build_auto_unwrap_strategy(kleisli: "KleisliProgram[Any, Any]") -> _AutoUnwrapStrategy:
    strategy = _AutoUnwrapStrategy()
    signature = getattr(kleisli, "__signature__", None)
    if signature is None:
        signature = _safe_signature(kleisli.func)
    type_hints = _safe_get_type_hints(getattr(kleisli, "__wrapped__", None))
    if not type_hints:
        type_hints = _safe_get_type_hints(kleisli.func)
    if signature is None:
        return strategy
    for param in signature.parameters.values():
        annotation = type_hints.get(param.name, param.annotation)
        should_unwrap = not _annotation_is_program(annotation)
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            strategy.positional.append(should_unwrap)
            if param.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD:
                strategy.keyword[param.name] = should_unwrap
        elif param.kind == inspect.Parameter.KEYWORD_ONLY:
            strategy.keyword[param.name] = should_unwrap
        elif param.kind == inspect.Parameter.VAR_POSITIONAL:
            strategy.var_positional = should_unwrap
        elif param.kind == inspect.Parameter.VAR_KEYWORD:
            strategy.var_keyword = should_unwrap
    return strategy

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

    def __post_init__(self) -> None:
        """Propagate signature and wrapper metadata from the wrapped callable."""

        wrapped = getattr(self.func, "__wrapped__", self.func)

        if not hasattr(self, "__wrapped__"):
            object.__setattr__(self, "__wrapped__", wrapped)

        try:
            signature_source = getattr(self, "__wrapped__")
            signature = inspect.signature(signature_source)
        except (TypeError, ValueError):
            try:
                signature = inspect.signature(self.func)
            except (TypeError, ValueError):
                signature = None

        if signature is not None and not hasattr(self, "__signature__"):
            object.__setattr__(self, "__signature__", signature)

        annotations = getattr(wrapped, "__annotations__", None)
        if annotations is None:
            annotations = getattr(self.func, "__annotations__", None)
        if annotations is not None and not hasattr(self, "__annotations__"):
            object.__setattr__(self, "__annotations__", dict(annotations))

        for attr in ("__name__", "__qualname__", "__doc__", "__module__"):
            if not hasattr(self, attr):
                value = getattr(wrapped, attr, getattr(self.func, attr, None))
                if value is not None:
                    object.__setattr__(self, attr, value)


    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Program[T]:
        """
        Call the Kleisli arrow, automatically unwrapping any Program arguments.

        This method uses the Gather effect to efficiently unwrap multiple Program
        arguments at once, then passes the unwrapped values to the underlying function.
        Parameters annotated as Program[...] opt out of auto-unwrapping so the callee
        can manage those Program instances manually.
        """

        strategy = _build_auto_unwrap_strategy(self)

        @wraps(self.func)
        def unwrapping_generator() -> Generator[Effect | Program, Any, T]:
            program_args: list[Program[Any]] = []
            program_indices: list[int] = []
            regular_args: list[Any | None] = list(args)

            for index, arg in enumerate(args):
                should_unwrap = strategy.should_unwrap_positional(index)
                if should_unwrap and isinstance(arg, Program):
                    program_args.append(arg)
                    program_indices.append(index)
                    regular_args[index] = None
                else:
                    regular_args[index] = arg

            program_kwargs: dict[str, Program[Any]] = {}
            regular_kwargs: dict[str, Any] = {}

            for key, value in kwargs.items():
                should_unwrap = strategy.should_unwrap_keyword(key)
                if should_unwrap and isinstance(value, Program):
                    program_kwargs[key] = value
                else:
                    regular_kwargs[key] = value

            if program_args or program_kwargs:
                if program_args:
                    unwrapped_args = yield Gather(*program_args)
                    for idx, unwrapped_value in zip(
                        program_indices, unwrapped_args, strict=False
                    ):
                        regular_args[idx] = unwrapped_value

                if program_kwargs:
                    unwrapped_kwargs = yield GatherDict(program_kwargs)
                    regular_kwargs.update(unwrapped_kwargs)

            result_program = self.func(*regular_args, **regular_kwargs)

            if isinstance(result_program, Program):
                result = yield result_program
                return result
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
