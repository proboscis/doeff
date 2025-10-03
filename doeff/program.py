"""
Program class for the doeff system.

This module contains the Program wrapper class that represents a lazy computation.
"""

from __future__ import annotations

import inspect
from abc import ABC
from collections.abc import Callable, Generator, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from doeff.effects._program_types import ProgramLike
    from doeff.types import Effect, EffectBase, Maybe

T = TypeVar("T")
U = TypeVar("U")
V = TypeVar("V")


class ProgramBase(ABC, Generic[T]):
    """Runtime base class for all doeff programs (effects and Kleisli calls)."""

    def __class_getitem__(cls, item):
        """Allow ``Program[T]`` generic-style annotations."""
        return super().__class_getitem__(item)

    @staticmethod
    def pure(value: T) -> "Program[T]":
        from doeff.effects.pure import PureEffect

        return PureEffect(value=value)

    @staticmethod
    def of(value: T) -> "Program[T]":
        return ProgramBase.pure(value)

    @staticmethod
    def lift(value: "Program[U]" | U) -> "Program[U]":
        if isinstance(value, ProgramBase):
            return value  # type: ignore[return-value]
        return ProgramBase.pure(value)  # type: ignore[return-value]

    @staticmethod
    def from_effect(effect: "Effect") -> "Effect":
        return effect

    @staticmethod
    def first_success(*programs: "ProgramLike[T]") -> "KleisliProgramCall[T]":
        if not programs:
            raise ValueError("Program.first_success requires at least one program")

        from doeff.effects import first_success_effect

        def first_success_generator():
            effect = first_success_effect(*programs)
            value = yield effect
            return value

        return KleisliProgramCall.create_anonymous(first_success_generator)

    @staticmethod
    def first_some(*programs: "ProgramLike[V]") -> "KleisliProgramCall[Maybe[V]]":
        if not programs:
            raise ValueError("Program.first_some requires at least one program")

        from doeff.types import EffectBase, Maybe

        def first_some_generator():
            for candidate in programs:
                if isinstance(candidate, (ProgramBase, EffectBase)):
                    normalized = candidate
                else:
                    raise TypeError(
                        "Program.first_some expects Program or Effect candidates"
                    )
                value = yield normalized

                maybe = value if isinstance(value, Maybe) else Maybe.from_optional(value)

                if maybe.is_some():
                    return maybe

            return Maybe.from_optional(None)

        return KleisliProgramCall.create_anonymous(first_some_generator)

    @staticmethod
    def sequence(programs: list["Program[T]"]) -> "KleisliProgramCall[list[T]]":
        from doeff.effects import gather

        def sequence_generator():
            effect = gather(*programs)
            results = yield effect
            return list(results)

        return KleisliProgramCall.create_anonymous(sequence_generator)

    @staticmethod
    def traverse(
        items: list[T],
        func: Callable[[T], "Program[U]"],
    ) -> "KleisliProgramCall[list[U]]":
        programs = [func(item) for item in items]
        return ProgramBase.sequence(programs)

    @staticmethod
    def list(*values: Iterable["Program[U]" | U]) -> "KleisliProgramCall[list[U]]":
        programs = [ProgramBase.lift(value) for value in values]
        return ProgramBase.sequence(programs)

    @staticmethod
    def tuple(*values: Iterable["Program[U]" | U]) -> "KleisliProgramCall[tuple[U, ...]]":
        return ProgramBase.list(*values).map(lambda items: tuple(items))

    @staticmethod
    def set(*values: Iterable["Program[U]" | U]) -> "KleisliProgramCall[set[U]]":
        return ProgramBase.list(*values).map(lambda items: set(items))

    @staticmethod
    def dict(
        *mapping: Mapping[Any, "Program[V]" | V] | Iterable[tuple[Any, "Program[V]" | V]],
        **kwargs: "Program[V]" | V,
    ) -> "KleisliProgramCall[dict[Any, V]]":
        raw = dict(*mapping, **kwargs)

        from doeff.effects import gather_dict

        def dict_generator():
            program_map = {
                key: ProgramBase.lift(value)
                for key, value in raw.items()
            }
            effect = gather_dict(program_map)
            result = yield effect
            return dict(result)

        return KleisliProgramCall.create_anonymous(dict_generator)


@runtime_checkable
class ProgramProtocol(Protocol[T]):
    """
    Protocol for all executable computations in doeff.

    This protocol defines the core interface that both Effects and KleisliProgramCalls
    implement, allowing them to be composed uniformly.
    """

    def map(self, f: Callable[[T], U]) -> ProgramProtocol[U]:
        """Map a function over the result of this program (functor map)."""
        ...

    def flat_map(self, f: Callable[[T], ProgramProtocol[U]]) -> ProgramProtocol[U]:
        """Monadic bind operation - chain programs sequentially."""
        ...

    def intercept(
        self, transform: Callable[[Effect], Effect | ProgramProtocol]
    ) -> ProgramProtocol[T]:
        """Apply transform to all yielded effects in this program."""
        ...


@dataclass(frozen=True)
class KleisliProgramCall(ProgramBase, Generic[T]):
    """
    Compound computation with bound arguments (partial application).

    Like partial application: holds the generator-creating function + its arguments.
    Call to_generator() to create the actual generator.

    This is distinct from KleisliProgram:
    - KleisliProgram: Unbound (holds Callable[P, Generator[Program, Any, T]])
    - KleisliProgramCall: Bound (holds SAME function + args)
    """

    generator_func: Callable[..., Generator[Effect | Program, Any, T]]
    # ^ The generator-creating function (same as in KleisliProgram!)
    args: tuple  # Bound arguments
    kwargs: dict[str, Any]

    # Metadata (source of the call)
    kleisli_source: Any = None  # The KleisliProgram that created this (type: KleisliProgram | None)
    function_name: str = "<anonymous>"
    created_at: Any = None  # EffectCreationContext | None

    def to_generator(self) -> Generator[Effect | Program, Any, T]:
        """Create generator by calling generator_func(*args, **kwargs)."""
        return self.generator_func(*self.args, **self.kwargs)

    @classmethod
    def create_from_kleisli(
        cls,
        generator_func: Callable[..., Generator[Effect | Program, Any, T]],
        kleisli: Any,  # KleisliProgram
        args: tuple,
        kwargs: dict[str, Any],
        function_name: str,
        created_at: Any = None,  # EffectCreationContext | None
    ) -> KleisliProgramCall[T]:
        """Create from KleisliProgram.__call__ (knows its source)."""
        return cls(
            generator_func=generator_func,
            args=tuple(args),
            kwargs=dict(kwargs),
            kleisli_source=kleisli,
            function_name=function_name,
            created_at=created_at,
        )

    @classmethod
    def create_anonymous(
        cls,
        generator_func: Callable[..., Generator[Effect | Program, Any, T]],
        args: tuple = (),
        kwargs: dict[str, Any] | None = None,
    ) -> KleisliProgramCall[T]:
        """Create from map/flat_map (no source KleisliProgram)."""
        return cls(
            generator_func=generator_func,
            args=tuple(args),
            kwargs=dict(kwargs or {}),
            kleisli_source=None,
            function_name="<anonymous>",
            created_at=None,
        )

    @classmethod
    def create_derived(
        cls,
        generator_func: Callable[..., Generator[Effect | Program, Any, T]],
        parent: KleisliProgramCall,
        args: tuple | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> KleisliProgramCall[T]:
        """Create from transforming another KPCall (preserve metadata)."""
        return cls(
            generator_func=generator_func,
            args=tuple(args) if args is not None else parent.args,
            kwargs=dict(kwargs) if kwargs is not None else parent.kwargs,
            kleisli_source=parent.kleisli_source,
            function_name=parent.function_name,
            created_at=parent.created_at,
        )

    def map(self, f: Callable[[T], U]) -> "KleisliProgramCall[U]":
        """Map over result."""

        def mapped_gen(*_args: Any, **_kwargs: Any) -> Generator[Effect | Program, Any, U]:
            value = yield self
            return f(value)

        return KleisliProgramCall.create_derived(mapped_gen, parent=self)

    def flat_map(self, f: Callable[[T], ProgramProtocol[U]]) -> "KleisliProgramCall[U]":
        """Monadic bind operation."""

        def flatmapped_gen(*_args: Any, **_kwargs: Any) -> Generator[Effect | Program, Any, U]:
            value = yield self
            next_prog = f(value)
            result = yield next_prog
            return result

        return KleisliProgramCall.create_derived(flatmapped_gen, parent=self)

    def intercept(
        self, transform: Callable[[Effect], Effect | ProgramProtocol]
    ) -> KleisliProgramCall[T]:
        """Apply transform to all yielded effects."""
        return _InterceptedProgram.compose(self, (transform,))  # type: ignore



@dataclass(frozen=True)
class _InterceptedProgram(KleisliProgramCall[T]):
    """Program wrapper that composes multiple intercept transforms exactly once."""

    _base_program: "Program[T]" = None  # type: ignore[assignment]
    _transforms: tuple[Callable[["Effect"], "Effect | Program"], ...] = ()

    def __init__(
        self,
        base: "Program[T]",
        transforms: tuple[Callable[["Effect"], "Effect | Program"], ...],
    ) -> None:

        if isinstance(base, KleisliProgramCall):
            args = base.args
            kwargs = base.kwargs
            kleisli_source = base.kleisli_source
            function_name = base.function_name
            created_at = base.created_at
        else:
            args = ()
            kwargs = {}
            kleisli_source = None
            function_name = "<intercepted>"
            created_at = getattr(base, "created_at", None)

        def generator_func(*_call_args: Any, **_call_kwargs: Any) -> Generator["Effect | Program", Any, T]:
            return self._intercept_generator(base, transforms)

        object.__setattr__(self, "generator_func", generator_func)
        object.__setattr__(self, "args", args)
        object.__setattr__(self, "kwargs", kwargs)
        object.__setattr__(self, "kleisli_source", kleisli_source)
        object.__setattr__(self, "function_name", function_name)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "_base_program", base)
        object.__setattr__(self, "_transforms", transforms)

    @property
    def base_program(self) -> "Program[T]":
        return self._base_program  # type: ignore[attr-defined]

    @property
    def transforms(self) -> tuple[Callable[["Effect"], "Effect | Program"], ...]:
        return self._transforms  # type: ignore[attr-defined]

    @classmethod
    def compose(
        cls,
        program: "Program[T]" | KleisliProgramCall[T],
        transforms: tuple[Callable[["Effect"], "Effect | Program"], ...],
    ) -> "Program[T]":
        if not transforms:
            # If no transforms, return as-is (KleisliProgramCall is already a Program)
            return program  # type: ignore

        # KleisliProgramCall is already a Program, use it as base
        if isinstance(program, KleisliProgramCall):
            base_program: "Program[T]" = program  # type: ignore[assignment]
        elif isinstance(program, cls):
            base_program = program.base_program
            combined = program.transforms + transforms
            return cls(base_program, combined)
        else:
            base_program = program

        return cls(base_program, transforms)

    def intercept(
        self, transform: Callable[["Effect"], "Effect | Program"]
    ) -> "Program[T]":
        return self.compose(self.base_program, self.transforms + (transform,))

    @staticmethod
    def _intercept_generator(
        base: "Program[T]",
        transforms: tuple[Callable[["Effect"], "Effect | Program"], ...],
    ) -> Generator["Effect | Program", Any, T]:
        from doeff.types import EffectBase

        gen = _InterceptedProgram._program_to_generator(base)
        try:
            current = next(gen)
        except StopIteration as exc:
            return exc.value

        kleisli_transform = _InterceptedProgram._compose_kleisli(transforms)

        while True:
            if isinstance(current, KleisliProgramCall):
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
    def _program_to_generator(
        base: "Program[T]",
    ) -> Generator["Effect | Program", Any, T]:
        """Return a generator for the provided program instance."""

        if isinstance(base, KleisliProgramCall):
            return base.to_generator()

        generator_factory = getattr(base, "generator_func", None)
        if callable(generator_factory):
            return generator_factory()

        raise TypeError(
            "Cannot intercept value that does not expose a generator: "
            f"{type(base).__name__}"
        )

    @staticmethod
    def _compose_kleisli(
        transforms: tuple[Callable[["Effect"], "Effect | Program"], ...]
    ) -> Callable[["EffectBase"], "Program[EffectBase]"]:
        from doeff.types import EffectBase

        lifted = [_InterceptedProgram._lift_transform(transform) for transform in transforms]

        def combined(effect: EffectBase) -> "Program[EffectBase]":
            program: "Program[EffectBase]" = Program.pure(effect)
            for lift in lifted:
                program = program.flat_map(lift)
            return program

        return combined

    @staticmethod
    def _lift_transform(
        transform: Callable[["Effect"], "Effect | Program"]
    ) -> Callable[["EffectBase"], "Program[EffectBase]"]:
        from doeff.types import EffectBase

        def lifted(effect: EffectBase) -> "Program[EffectBase]":
            result = transform(effect)

            if isinstance(result, KleisliProgramCall):
                return result.flat_map(_InterceptedProgram._ensure_effect_program)

            if isinstance(result, EffectBase):
                return Program.pure(result)

            raise TypeError(
                "Intercept transform must return Effect or Program yielding Effect, "
                f"got {type(result).__name__}"
            )

        return lifted


    @staticmethod
    def _ensure_effect_program(value: Any) -> "Program[EffectBase]":
        from doeff.types import EffectBase

        if isinstance(value, EffectBase):
            return Program.pure(value)
        raise TypeError(
            "Intercept transform must resolve to an Effect, got "
            f"{type(value).__name__}"
        )

Program = ProgramBase

__all__ = ["Program", "ProgramProtocol", "KleisliProgramCall"]
