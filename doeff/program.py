"""
Program class for the doeff system.

This module contains the Program wrapper class that represents a lazy computation.
"""

from __future__ import annotations

import inspect
import types
from abc import ABC
from collections.abc import Callable, Generator, Iterable, Mapping
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ForwardRef,
    Generic,
    Protocol,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    runtime_checkable,
)

if TYPE_CHECKING:
    from doeff.effects._program_types import ProgramLike
    from doeff.types import Effect, Maybe

T = TypeVar("T")
U = TypeVar("U")
V = TypeVar("V")


class _AutoUnwrapStrategy:
    """Describe which arguments should be auto-unwrapped for a Kleisli call."""

    __slots__ = ("keyword", "positional", "var_keyword", "var_positional")

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
    # Handle quoted strings from __future__ annotations in Python 3.14+
    if (stripped.startswith("'") and stripped.endswith("'")) or (
        stripped.startswith('"') and stripped.endswith('"')
    ):
        stripped = stripped[1:-1]
    if "|" in stripped:
        return any(_string_annotation_is_program(part) for part in stripped.split("|"))
    if stripped.startswith("Optional[") and stripped.endswith("]"):
        return _string_annotation_is_program(stripped[9:-1])
    if stripped.startswith("typing.Optional[") and stripped.endswith("]"):
        return _string_annotation_is_program(stripped[len("typing.Optional[") : -1])
    if stripped.startswith("Annotated[") and stripped.endswith("]"):
        inner = stripped[len("Annotated[") : -1]
        first_part = inner.split(",", 1)[0]
        return _string_annotation_is_program(first_part)
    normalized = stripped.replace(" ", "")
    return (
        normalized == "Program"
        or normalized.startswith("Program[")
        or normalized.startswith("doeff.program.Program")
        or normalized == "ProgramLike"
        or normalized.startswith("ProgramLike[")
        or normalized == "ProgramBase"
        or normalized.startswith("ProgramBase[")
    )


def _string_annotation_is_effect(annotation_text: str) -> bool:
    if not annotation_text:
        return False
    stripped = annotation_text.strip()
    if not stripped:
        return False
    # Handle quoted strings from __future__ annotations in Python 3.14+
    if (stripped.startswith("'") and stripped.endswith("'")) or (
        stripped.startswith('"') and stripped.endswith('"')
    ):
        stripped = stripped[1:-1]
    if "|" in stripped:
        return any(_string_annotation_is_effect(part) for part in stripped.split("|"))
    if stripped.startswith("Optional[") and stripped.endswith("]"):
        return _string_annotation_is_effect(stripped[9:-1])
    if stripped.startswith("typing.Optional[") and stripped.endswith("]"):
        return _string_annotation_is_effect(stripped[len("typing.Optional[") : -1])
    if stripped.startswith("Annotated[") and stripped.endswith("]"):
        inner = stripped[len("Annotated[") : -1]
        first_part = inner.split(",", 1)[0]
        return _string_annotation_is_effect(first_part)
    normalized = stripped.replace(" ", "")
    return (
        normalized == "Effect"
        or normalized == "EffectBase"
        or normalized.startswith("Effect[")
        or normalized.startswith("doeff.types.Effect")
        or normalized.startswith("doeff.types.EffectBase")
    )


def _annotation_is_program(annotation: Any) -> bool:
    if annotation is inspect._empty:
        return False

    from doeff.types import EffectBase as EffectBaseType

    program_type = globals().get("Program", ProgramBase)

    if annotation in (program_type, ProgramBase):
        return True
    # Check for subclasses of ProgramBase (excluding EffectBase subclasses)
    if isinstance(annotation, type) and issubclass(annotation, ProgramBase):
        if not issubclass(annotation, EffectBaseType):
            return True
    if isinstance(annotation, ForwardRef):
        return _string_annotation_is_program(annotation.__forward_arg__)
    if isinstance(annotation, str):
        return _string_annotation_is_program(annotation)
    origin = get_origin(annotation)
    if origin in (program_type, ProgramBase):
        return True
    # Check origin for subclasses (e.g., MyProgram[T])
    if isinstance(origin, type) and issubclass(origin, ProgramBase):
        if not issubclass(origin, EffectBaseType):
            return True
    if origin is Annotated:
        args = get_args(annotation)
        if args:
            return _annotation_is_program(args[0])
        return False
    union_type = getattr(types, "UnionType", None)
    if origin is Union or (union_type is not None and origin is union_type):
        return any(_annotation_is_program(arg) for arg in get_args(annotation))
    return False


def _annotation_is_effect(annotation: Any) -> bool:
    if annotation is inspect._empty:
        return False
    from doeff.types import Effect, EffectBase  # Local import to avoid global dependency

    if annotation in (Effect, EffectBase):
        return True
    # Check for subclasses of EffectBase
    if isinstance(annotation, type) and issubclass(annotation, EffectBase):
        return True
    if isinstance(annotation, ForwardRef):
        return _string_annotation_is_effect(annotation.__forward_arg__)
    if isinstance(annotation, str):
        return _string_annotation_is_effect(annotation)
    origin = get_origin(annotation)
    if origin in (Effect, EffectBase):
        return True
    # Check origin for subclasses (e.g., MyEffect[T])
    if isinstance(origin, type) and issubclass(origin, EffectBase):
        return True
    if origin is Annotated:
        args = get_args(annotation)
        if args:
            return _annotation_is_effect(args[0])
        return False
    union_type = getattr(types, "UnionType", None)
    if origin is Union or (union_type is not None and origin is union_type):
        return any(_annotation_is_effect(arg) for arg in get_args(annotation))
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


def _build_auto_unwrap_strategy(kleisli: Any) -> _AutoUnwrapStrategy:
    strategy = _AutoUnwrapStrategy()
    signature = getattr(kleisli, "__signature__", None)
    if signature is None:
        signature = _safe_signature(getattr(kleisli, "func", None))
    metadata_source = getattr(kleisli, "_metadata_source", None)
    type_hints = _safe_get_type_hints(metadata_source)
    if not type_hints:
        type_hints = _safe_get_type_hints(getattr(kleisli, "func", None))
    if signature is None:
        return strategy
    for param in signature.parameters.values():
        annotation = type_hints.get(param.name, param.annotation)
        is_program = _annotation_is_program(annotation)
        is_effect = _annotation_is_effect(annotation)
        should_unwrap = not (is_program or is_effect)
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


class ProgramBase(ABC, Generic[T]):
    """Runtime base class for all doeff programs (effects and Kleisli calls)."""

    def __class_getitem__(cls, item):
        """Allow ``Program[T]`` generic-style annotations."""
        return super().__class_getitem__(item)

    def __getattr__(self, name: str) -> Program[Any]:
        """Lazily project an attribute from the eventual program result."""

        if name.startswith("__"):
            raise AttributeError(name)

        def mapper(value: Any) -> Any:
            try:
                return getattr(value, name)
            except AttributeError as exc:  # pragma: no cover - re-raise with context
                raise AttributeError(
                    f"{type(value).__name__} object has no attribute '{name}'"
                ) from exc

        return self.map(mapper)

    def __getitem__(self, key: Any) -> Program[Any]:
        """Lazily project an item from the eventual program result."""

        return self.map(lambda value: value[key])

    def __call__(self, *args: Any, **kwargs: Any) -> Program[Any]:
        """Invoke the eventual callable result with the provided arguments."""

        def invoke_callable(func: Any) -> Program[Any]:
            if not callable(func):
                raise TypeError(f"Program result {func!r} is not callable")
            arg_programs = [ProgramBase.lift(arg) for arg in args]
            kw_programs = {name: ProgramBase.lift(value) for name, value in kwargs.items()}

            from doeff.effects import gather

            def gather_inputs() -> Generator[
                Effect | Program, Any, tuple[list[Any], dict[str, Any]]
            ]:
                resolved_args = yield gather(*arg_programs)
                kw_keys = list(kw_programs.keys())
                kw_values = yield gather(*kw_programs.values())
                resolved_kwargs = dict(zip(kw_keys, kw_values, strict=False))
                return list(resolved_args), resolved_kwargs

            def call_program() -> Generator[Effect | Program, Any, Any]:
                from doeff.types import EffectBase

                resolved_args, resolved_kwargs = yield GeneratorProgram(gather_inputs)
                result = func(*resolved_args, **resolved_kwargs)
                if isinstance(result, (ProgramBase, EffectBase)):
                    return (yield result)
                return result

            return GeneratorProgram(call_program)

        return self.flat_map(invoke_callable)

    def map(self, f: Callable[[T], U]) -> Program[U]:
        """Map a function over this program's result."""

        if not callable(f):
            raise TypeError("mapper must be callable")

        def factory() -> Generator[Effect | Program, Any, U]:
            value = yield self
            return f(value)

        return GeneratorProgram(factory)

    def flat_map(self, f: Callable[[T], Program[U]]) -> Program[U]:
        """Monadic bind operation."""

        if not callable(f):
            raise TypeError("binder must be callable returning a Program")

        def factory() -> Generator[Effect | Program, Any, U]:
            from doeff.types import EffectBase

            value = yield self
            next_prog = f(value)
            if not isinstance(next_prog, (ProgramBase, EffectBase)):
                raise TypeError(f"binder must return a Program; got {type(next_prog).__name__}")
            result = yield next_prog
            return result

        return GeneratorProgram(factory)

    def and_then_k(self, binder: Callable[[T], Program[U]]) -> Program[U]:
        """Alias for flat_map for Kleisli-style composition."""

        return self.flat_map(binder)

    @staticmethod
    def pure(value: T) -> Program[T]:
        from doeff.effects.pure import PureEffect

        return PureEffect(value=value)

    @staticmethod
    def of(value: T) -> Program[T]:
        return ProgramBase.pure(value)

    @staticmethod
    def lift(value: Program[U] | U) -> Program[U]:
        from doeff.types import EffectBase

        if isinstance(value, (ProgramBase, EffectBase)):
            return value  # type: ignore[return-value]
        return ProgramBase.pure(value)  # type: ignore[return-value]

    @staticmethod
    def first_some(*programs: ProgramLike[V]) -> Program[Maybe[V]]:
        if not programs:
            raise ValueError("Program.first_some requires at least one program")

        from doeff.types import EffectBase, Maybe

        def first_some_generator():
            for candidate in programs:
                if isinstance(candidate, (ProgramBase, EffectBase)):
                    normalized = candidate
                else:
                    raise TypeError("Program.first_some expects Program or Effect candidates")
                value = yield normalized

                maybe = value if isinstance(value, Maybe) else Maybe.from_optional(value)

                if maybe.is_some():
                    return maybe

            return Maybe.from_optional(None)

        return GeneratorProgram(first_some_generator)

    @staticmethod
    def sequence(programs: list[Program[T]]) -> Program[list[T]]:
        from doeff.effects import gather

        def sequence_generator():
            effect = gather(*programs)
            results = yield effect
            return list(results)

        return GeneratorProgram(sequence_generator)

    @staticmethod
    def traverse(
        items: list[T],
        func: Callable[[T], Program[U]],
    ) -> Program[list[U]]:
        programs = [func(item) for item in items]
        return ProgramBase.sequence(programs)

    @staticmethod
    def list(*values: Program[U] | U) -> Program[list[U]]:
        programs = [ProgramBase.lift(value) for value in values]
        return ProgramBase.sequence(programs)

    @staticmethod
    def tuple(*values: Program[U] | U) -> Program[tuple[U, ...]]:
        return ProgramBase.list(*values).map(lambda items: tuple(items))

    @staticmethod
    def set(*values: Program[U] | U) -> Program[set[U]]:
        return ProgramBase.list(*values).map(lambda items: set(items))

    @staticmethod
    def dict(
        *mapping: Mapping[Any, Program[V] | V] | Iterable[tuple[Any, Program[V] | V]],
        **kwargs: Program[V] | V,
    ) -> Program[dict[Any, V]]:
        raw = dict(*mapping, **kwargs)

        from doeff.effects import gather

        def dict_generator():
            program_map = {key: ProgramBase.lift(value) for key, value in raw.items()}
            keys = list(program_map.keys())
            values = yield gather(*program_map.values())
            return dict(zip(keys, values, strict=False))

        return GeneratorProgram(dict_generator)


@dataclass
class GeneratorProgram(ProgramBase[T]):
    """Program backed by a generator factory."""

    factory: Callable[[], Generator[Effect | Program, Any, T]]
    created_at: Any | None = None

    def to_generator(self) -> Generator[Effect | Program, Any, T]:
        return self.factory()


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


@dataclass(frozen=True)
class KleisliProgramCall(ProgramBase, Generic[T]):
    """Bound invocation of a KleisliProgram with captured arguments."""

    kleisli_source: Any  # KleisliProgram to execute
    args: tuple
    kwargs: dict[str, Any]

    function_name: str = "<anonymous>"
    created_at: Any = None
    auto_unwrap_strategy: _AutoUnwrapStrategy | None = None
    execution_kernel: Callable[..., Generator[Effect | Program, Any, T]] | None = None

    def to_generator(self) -> Generator[Effect | Program, Any, T]:
        """Create generator by invoking the captured Kleisli program."""

        from doeff.effects import Gather

        kleisli = self.kleisli_source
        strategy = self.auto_unwrap_strategy
        if strategy is None and kleisli is not None:
            strategy = _build_auto_unwrap_strategy(kleisli)
        if strategy is None:
            strategy = _AutoUnwrapStrategy()

        kernel = self.execution_kernel
        if kernel is None and kleisli is not None:
            kernel = getattr(kleisli, "func", None)
        if kernel is None:
            raise TypeError("Execution kernel unavailable for KleisliProgramCall")

        args_tuple = self.args
        kwargs_dict = self.kwargs

        from doeff.types import EffectBase

        def generator() -> Generator[Effect | Program, Any, T]:
            resolvable = (ProgramBase, EffectBase)
            program_args: list[ProgramBase[Any]] = []
            program_indices: list[int] = []
            regular_args: list[Any | None] = list(args_tuple)

            for index, arg in enumerate(args_tuple):
                should_unwrap = strategy.should_unwrap_positional(index)
                if should_unwrap and isinstance(arg, resolvable):
                    program_args.append(arg)
                    program_indices.append(index)
                    regular_args[index] = None
                else:
                    regular_args[index] = arg

            program_kwargs: dict[str, ProgramBase[Any]] = {}
            regular_kwargs: dict[str, Any] = {}

            for key, value in kwargs_dict.items():
                should_unwrap = strategy.should_unwrap_keyword(key)
                if should_unwrap and isinstance(value, resolvable):
                    program_kwargs[key] = value
                else:
                    regular_kwargs[key] = value

            if program_args:
                unwrapped_args = yield Gather(*program_args)
                for idx, unwrapped_value in zip(program_indices, unwrapped_args, strict=False):
                    regular_args[idx] = unwrapped_value

            if program_kwargs:
                keys = list(program_kwargs.keys())
                values = yield Gather(*program_kwargs.values())
                unwrapped_kwargs = dict(zip(keys, values, strict=False))
                regular_kwargs.update(unwrapped_kwargs)

            final_args = tuple(regular_args)
            result = kernel(*final_args, **regular_kwargs)

            if isinstance(result, (ProgramBase, EffectBase)):
                resolved = yield result
                return resolved

            generator_obj = result
            try:
                current = next(generator_obj)
            except StopIteration as stop_exc:
                return stop_exc.value

            while True:
                try:
                    sent_value = yield current
                except GeneratorExit:
                    generator_obj.close()
                    raise
                except BaseException as e:
                    try:
                        current = generator_obj.throw(e)
                    except StopIteration as stop_exc:
                        return stop_exc.value
                    continue
                try:
                    current = generator_obj.send(sent_value)
                except StopIteration as stop_exc:
                    return stop_exc.value

        return generator()

    @classmethod
    def create_from_kleisli(
        cls,
        kleisli: Any,  # KleisliProgram
        args: tuple,
        kwargs: dict[str, Any],
        function_name: str,
        created_at: Any = None,  # EffectCreationContext | None
    ) -> KleisliProgramCall[T]:
        """Create from KleisliProgram.__call__ (knows its source)."""

        strategy = _build_auto_unwrap_strategy(kleisli)
        return cls(
            kleisli_source=kleisli,
            args=tuple(args),
            kwargs=dict(kwargs),
            function_name=function_name,
            created_at=created_at,
            auto_unwrap_strategy=strategy,
            execution_kernel=getattr(kleisli, "func", None),
        )

    def map(self, f: Callable[[T], U]) -> Program[U]:
        """Map over result.

        SPEC-TYPES-001: map on any DoExpr returns a derived DoThunk.
        """

        if not callable(f):
            raise TypeError("mapper must be callable")

        def mapped_gen() -> Generator[Effect | Program, Any, U]:
            value = yield self
            return f(value)

        return GeneratorProgram(mapped_gen)

    def flat_map(self, f: Callable[[T], ProgramProtocol[U]]) -> Program[U]:
        """Monadic bind operation.

        SPEC-TYPES-001: flat_map on any DoExpr returns a derived DoThunk.
        """

        if not callable(f):
            raise TypeError("binder must be callable returning Program/Effect")

        def flatmapped_gen() -> Generator[Effect | Program, Any, U]:
            from doeff.types import EffectBase

            value = yield self
            next_prog = f(value)
            if not isinstance(next_prog, (ProgramBase, EffectBase)):
                raise TypeError(
                    f"binder must return Program/Effect; got {type(next_prog).__name__}"
                )
            result = yield next_prog
            return result

        return GeneratorProgram(flatmapped_gen)


DoExpr = ProgramBase
DoThunk = ProgramBase
Program = DoExpr

__all__ = [
    "DoExpr",
    "DoThunk",
    "GeneratorProgram",
    "KleisliProgramCall",
    "Program",
    "ProgramProtocol",
]
