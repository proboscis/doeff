"""
Kleisli arrow implementation for the doeff system.

This module contains the KleisliProgram class that enables automatic
unwrapping of Program arguments for natural composition.
"""

from __future__ import annotations

import inspect
import types
import warnings
from collections.abc import Callable
from collections.abc import Callable as TypingCallable
from dataclasses import dataclass
from functools import wraps
from typing import Any, Generic, ParamSpec, TypeVar, get_type_hints

from doeff.program import (
    Program,
    ProgramBase,
    _build_auto_unwrap_strategy,
    _is_effect_annotation_kind,
)

P = ParamSpec("P")
T = TypeVar("T")
U = TypeVar("U")


@dataclass(frozen=True)
class KleisliProgram(Generic[P, T]):
    """
    Thin wrapper around a callable representing a Kleisli arrow.

    The callable stored in ``func`` is expected to produce a Program when invoked
    with fully unwrapped arguments. Argument unwrapping now happens when the
    resulting call expression is executed, keeping this class as a lightweight
    data container.
    """

    func: Callable[P, Program[T]]

    def __post_init__(self) -> None:
        wrapped = getattr(self.func, "__wrapped__", self.func)
        object.__setattr__(self, "_metadata_source", wrapped)
        is_do_decorated = bool(getattr(self, "__doeff_do_decorated__", False))
        object.__setattr__(self, "_is_do_decorated", is_do_decorated)

        signature = _safe_signature(wrapped) or _safe_signature(self.func)
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

        strategy = _build_auto_unwrap_strategy(self)
        object.__setattr__(self, "_auto_unwrap_strategy", strategy)

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        return types.MethodType(self, instance)

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Program[T]:
        from doeff_vm import Apply, DoCtrlBase, Expand, Perform, Pure

        from doeff.types import EffectBase

        strategy = getattr(self, "_auto_unwrap_strategy", None)
        if strategy is None:
            strategy = _build_auto_unwrap_strategy(self)
            object.__setattr__(self, "_auto_unwrap_strategy", strategy)

        def classify_arg(arg: Any, should_unwrap: bool) -> Any:
            if should_unwrap and isinstance(arg, EffectBase):
                return Perform(arg)
            if should_unwrap and isinstance(arg, DoCtrlBase):
                return arg
            return Pure(arg)

        positional_args = []
        for index, arg in enumerate(args):
            should_unwrap = strategy.should_unwrap_positional(index)
            positional_args.append(classify_arg(arg, should_unwrap))

        keyword_args = {
            key: classify_arg(value, strategy.should_unwrap_keyword(key))
            for key, value in kwargs.items()
        }

        metadata_source = getattr(self, "_metadata_source", self.func)
        code_obj = getattr(metadata_source, "__code__", None)
        function_name = getattr(
            self, "__name__", getattr(metadata_source, "__name__", "<anonymous>")
        )
        metadata = {
            "function_name": function_name,
            "source_file": getattr(code_obj, "co_filename", "<unknown>"),
            "source_line": int(getattr(code_obj, "co_firstlineno", 0) or 0),
            "args_repr": f"args={tuple(args)!r}, kwargs={dict(kwargs)!r}",
            "program_call": None,
        }

        if bool(getattr(self, "_is_do_decorated", False)):
            generator_factory = getattr(self, "_doeff_generator_factory", None)
            if generator_factory is None:
                raise TypeError(
                    "@do KleisliProgram is missing _doeff_generator_factory (DoeffGeneratorFn)"
                )
            return Expand(Pure(generator_factory), positional_args, keyword_args, metadata)
        return Apply(Pure(self.func), positional_args, keyword_args, metadata)

    def partial(self, /, *args: P.args, **kwargs: P.kwargs) -> PartiallyAppliedKleisliProgram[P, T]:
        return PartiallyAppliedKleisliProgram(self, args, kwargs)

    def and_then_k(
        self,
        binder: TypingCallable[[T], Program[U]],
    ) -> KleisliProgram[P, U]:
        if not callable(binder):
            raise TypeError("binder must be callable returning a Program")

        @wraps(self.func)
        def composed(*args: P.args, **kwargs: P.kwargs) -> Program[U]:
            program = self(*args, **kwargs)
            if not hasattr(program, "and_then_k"):
                raise TypeError("Kleisli program must return a Program or Effect")
            return program.and_then_k(binder)

        return KleisliProgram(composed)

    def __rshift__(
        self,
        binder: TypingCallable[[T], Program[U]],
    ) -> KleisliProgram[P, U]:
        return self.and_then_k(binder)

    def fmap(
        self,
        mapper: TypingCallable[[T], U],
    ) -> KleisliProgram[P, U]:
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

    def partial(self, /, *args: Any, **kwargs: Any) -> PartiallyAppliedKleisliProgram[P, T]:
        merged_args = self._pre_args + args
        merged_kwargs = {**self._pre_kwargs, **kwargs}
        return PartiallyAppliedKleisliProgram(self._base, merged_args, merged_kwargs)


def _safe_signature(target: Any) -> inspect.Signature | None:
    try:
        return inspect.signature(target)
    except (TypeError, ValueError, NameError):
        # NameError: Python 3.14+ raises when forward reference annotations
        # cannot be resolved (e.g., class defined later in file)
        return None


def validate_do_handler_effect_annotation(handler: Any) -> None:
    if not bool(getattr(handler, "__doeff_do_decorated__", False)):
        return

    signature = getattr(handler, "__signature__", None)
    if signature is None:
        signature = _safe_signature(getattr(handler, "func", None)) or _safe_signature(handler)
    if signature is None:
        raise TypeError("@do handler must expose an inspectable signature")

    params = list(signature.parameters.values())
    if len(params) < 2:
        raise TypeError("@do handler must accept (effect, k)")

    effect_annotation = params[0].annotation
    if effect_annotation is inspect._empty or not _is_effect_annotation_kind(effect_annotation):
        raise TypeError("@do handler first parameter must be annotated as Effect")


__all__ = ["KleisliProgram", "PartiallyAppliedKleisliProgram"]


def _hydrate_future_annotations() -> None:
    """Resolve postponed annotations for ParamSpec-aware methods."""

    try:
        hints = get_type_hints(KleisliProgram.__call__, include_extras=True)
    except Exception as exc:  # pragma: no cover - defensive guard
        warnings.warn(f"Failed to hydrate KleisliProgram.__call__ annotations: {exc}", stacklevel=2)
        hints = {}
    if hints:
        KleisliProgram.__call__.__annotations__ = hints


_hydrate_future_annotations()
