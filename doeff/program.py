"""
Program class for the doeff system.

This module contains the Program wrapper class that represents a lazy computation.
"""

import inspect
import types
import warnings
from abc import ABC, ABCMeta
from collections.abc import Callable, Generator, Iterable, Mapping
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
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

try:
    import doeff_vm as _doeff_vm

    _RustDoExprBase = _doeff_vm.DoExpr
except Exception:  # pragma: no cover - fallback for docs/type tooling without native module
    _RustDoExprBase = object


def _safe_issubclass(candidate: Any, parent: Any) -> bool:
    """Return False for typing/generic alias objects that are not real classes."""
    try:
        return issubclass(candidate, parent)
    except TypeError:
        return False


def _is_program_annotation_kind(annotation: Any) -> bool:
    if annotation is inspect._empty:
        return False

    from doeff.types import EffectBase as EffectBaseType

    program_type = globals().get("Program", ProgramBase)

    if annotation in (program_type, ProgramBase, DoExpr):
        return True
    # Check for subclasses of ProgramBase (excluding EffectBase subclasses)
    if isinstance(annotation, type) and (
        _safe_issubclass(annotation, ProgramBase) or _safe_issubclass(annotation, DoExpr)
    ):
        if not _safe_issubclass(annotation, EffectBaseType):
            return True
    origin = get_origin(annotation)
    if origin in (program_type, ProgramBase, DoExpr):
        return True
    # Check origin for subclasses (e.g., MyProgram[T])
    if isinstance(origin, type) and (
        _safe_issubclass(origin, ProgramBase) or _safe_issubclass(origin, DoExpr)
    ):
        if not _safe_issubclass(origin, EffectBaseType):
            return True
    if origin is Annotated:
        args = get_args(annotation)
        if args:
            return _is_program_annotation_kind(args[0])
        return False
    union_type = getattr(types, "UnionType", None)
    if origin is Union or (union_type is not None and origin is union_type):
        return any(_is_program_annotation_kind(arg) for arg in get_args(annotation))
    return False


def _is_effect_annotation_kind(annotation: Any) -> bool:
    if annotation is inspect._empty:
        return False
    from doeff.types import Effect, EffectBase  # Local import to avoid global dependency

    if annotation in (Effect, EffectBase):
        return True
    # Check for subclasses of EffectBase
    if isinstance(annotation, type) and _safe_issubclass(annotation, EffectBase):
        return True
    origin = get_origin(annotation)
    if origin in (Effect, EffectBase):
        return True
    # Check origin for subclasses (e.g., MyEffect[T])
    if isinstance(origin, type) and _safe_issubclass(origin, EffectBase):
        return True
    if origin is Annotated:
        args = get_args(annotation)
        if args:
            return _is_effect_annotation_kind(args[0])
        return False
    union_type = getattr(types, "UnionType", None)
    if origin is Union or (union_type is not None and origin is union_type):
        return any(_is_effect_annotation_kind(arg) for arg in get_args(annotation))
    return False


def _safe_get_type_hints(target: Any) -> dict[str, Any]:
    if target is None:
        return {}
    try:
        return get_type_hints(target, include_extras=True)
    except Exception as exc:
        warnings.warn(f"Failed to resolve type hints for {target}: {exc}", stacklevel=2)
        return {}


def _safe_signature(target: Any) -> inspect.Signature | None:
    try:
        return inspect.signature(target)
    except (TypeError, ValueError, NameError):
        return None


def _build_auto_unwrap_strategy(kleisli: Any) -> Any:
    class _Strategy:
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

    strategy = _Strategy()
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
        is_program = _is_program_annotation_kind(annotation)
        is_effect = _is_effect_annotation_kind(annotation)
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


class DoExpr(_RustDoExprBase, ABC, Generic[T]):
    """Universal base for all doeff programs (pure data or computation)."""

    def __class_getitem__(cls, item):
        return super().__class_getitem__(item)


class DoCtrl(DoExpr[T]):
    """VM control primitives."""

    pass


def _is_rust_program_subclass(subclass: type[Any]) -> bool:
    try:
        import doeff_vm
    except ImportError:
        return False

    try:
        return issubclass(subclass, doeff_vm.DoExpr)
    except TypeError:
        return False


class _ProgramBaseMeta(ABCMeta):
    def __subclasscheck__(cls, subclass: type[Any]) -> bool:
        program_base = globals().get("ProgramBase")
        if program_base is not None and cls is program_base and _is_rust_program_subclass(subclass):
            return True
        return super().__subclasscheck__(subclass)

    def __instancecheck__(cls, instance: Any) -> bool:
        return cls.__subclasscheck__(instance.__class__) or super().__instancecheck__(instance)


def _make_generator_program(
    factory: "Callable[[], Generator[Effect | Program, Any, T]]",
) -> "GeneratorProgram[T]":
    return _GenProgramThunk(factory)


def _callable_metadata_dict(func: Callable[..., Any]) -> dict[str, Any]:
    code = getattr(func, "__code__", None)
    if code is None:
        raise TypeError(
            f"Cannot derive callback metadata for callable {func!r}: "
            "__code__ is missing. Provide a Python function with __code__."
        )

    function_name = getattr(code, "co_name", getattr(func, "__name__", "<anonymous>"))
    source_file = getattr(code, "co_filename", "<unknown>")
    source_line = int(getattr(code, "co_firstlineno", 0) or 0)

    return {
        "function_name": function_name,
        "source_file": source_file,
        "source_line": source_line,
    }


class ProgramBase(DoExpr[T], metaclass=_ProgramBaseMeta):
    """Runtime base class for doeff programs."""

    def __class_getitem__(cls, item):
        """Allow ``Program[T]`` generic-style annotations."""
        return super().__class_getitem__(item)

    def __getattr__(self, name: str) -> "Program[Any]":
        """Lazily project an attribute from the eventual program result."""

        if name.startswith("__"):
            raise AttributeError(name)
        if name == "to_generator":
            # Preserve generator-detection semantics: objects without a real
            # to_generator method must not fabricate one via projection.
            raise AttributeError(name)

        def mapper(value: Any) -> Any:
            try:
                return getattr(value, name)
            except AttributeError as exc:  # pragma: no cover - re-raise with context
                raise AttributeError(
                    f"{type(value).__name__} object has no attribute '{name}'"
                ) from exc

        return self.map(mapper)

    def __getitem__(self, key: Any) -> "Program[Any]":
        """Lazily project an item from the eventual program result."""

        return self.map(lambda value: value[key])

    def __call__(self, *args: Any, **kwargs: Any) -> "Program[Any]":
        """Invoke the eventual callable result with the provided arguments."""

        def invoke_callable(func: Any) -> "Program[Any]":
            if not callable(func):
                raise TypeError(f"Program result {func!r} is not callable")
            arg_programs = [ProgramBase.lift(arg) for arg in args]
            kw_programs = {name: ProgramBase.lift(value) for name, value in kwargs.items()}

            from doeff.effects import gather

            def gather_inputs() -> "Generator[Effect | Program, Any, tuple[list[Any], dict[str, Any]]]":
                resolved_args = yield gather(*arg_programs)
                kw_keys = list(kw_programs.keys())
                kw_values = yield gather(*kw_programs.values())
                resolved_kwargs = dict(zip(kw_keys, kw_values, strict=False))
                return list(resolved_args), resolved_kwargs

            def call_program() -> "Generator[Effect | Program, Any, Any]":
                from doeff.types import EffectBase

                resolved_args, resolved_kwargs = yield _make_generator_program(gather_inputs)
                result = func(*resolved_args, **resolved_kwargs)
                if isinstance(result, (ProgramBase, EffectBase)):
                    return (yield result)
                return result

            return _make_generator_program(call_program)

        return self.flat_map(invoke_callable)

    def map(self, f: Callable[[T], U]) -> "Program[U]":
        """Map a function over this program's result."""

        if not callable(f):
            raise TypeError("mapper must be callable")
        from doeff_vm import Map

        mapper_meta = _callable_metadata_dict(f)
        return Map(self, f, mapper_meta=mapper_meta)

    def flat_map(self, f: "Callable[[T], Program[U]]") -> "Program[U]":
        """Monadic bind operation."""

        if not callable(f):
            raise TypeError("binder must be callable returning a Program")
        from doeff_vm import DoExpr, FlatMap, Perform

        from doeff.types import EffectBase

        binder_meta = _callable_metadata_dict(f)

        def binder_factory(value: T) -> Any:
            bound = f(value)
            if isinstance(bound, EffectBase):
                bound = Perform(bound)
            if isinstance(bound, DoExpr):
                return bound.to_generator()
            raise TypeError(f"flat_map binder must return Program/Effect/DoCtrl; got {bound!r}")

        return FlatMap(self, binder_factory, binder_meta=binder_meta)

    def and_then_k(self, binder: "Callable[[T], Program[U]]") -> "Program[U]":
        """Alias for flat_map for Kleisli-style composition."""

        return self.flat_map(binder)

    @staticmethod
    def pure(value: T) -> "Program[T]":
        from doeff_vm import Pure

        return Pure(value=value)

    @staticmethod
    def of(value: T) -> "Program[T]":
        return ProgramBase.pure(value)

    @staticmethod
    def lift(value: "Program[U] | U") -> "Program[U]":
        from doeff_vm import DoExpr

        from doeff.types import EffectBase

        if isinstance(value, ProgramBase):
            return value  # type: ignore[return-value]
        if isinstance(value, DoExpr):
            return value  # type: ignore[return-value]
        if isinstance(value, EffectBase):
            from doeff.rust_vm import Perform

            return Perform(value)  # type: ignore[return-value]
        return ProgramBase.pure(value)  # type: ignore[return-value]

    @staticmethod
    def first_some(*programs: "ProgramLike[V]") -> "Program[Maybe[V]]":
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

        return _make_generator_program(first_some_generator)

    @staticmethod
    def sequence(programs: "list[Program[T]]") -> "Program[list[T]]":
        from doeff.effects.gather import gather
        from doeff.effects.spawn import spawn

        def sequence_generator():
            tasks = []
            for prog in programs:
                tasks.append((yield spawn(prog)))
            return list((yield gather(*tasks)))

        return _make_generator_program(sequence_generator)

    @staticmethod
    def traverse(
        items: list[T],
        func: "Callable[[T], Program[U]]",
    ) -> "Program[list[U]]":
        programs = [func(item) for item in items]
        return ProgramBase.sequence(programs)

    @staticmethod
    def list(*values: "Program[U] | U") -> "Program[list[U]]":
        from doeff._collection_combinators import _list

        return _list(*values)

    @staticmethod
    def tuple(*values: "Program[U] | U") -> "Program[tuple[U, ...]]":
        from doeff._collection_combinators import _tuple

        return _tuple(*values)

    @staticmethod
    def set(*values: "Program[U] | U") -> "Program[set[U]]":
        from doeff._collection_combinators import _set

        return _set(*values)

    @staticmethod
    def dict(
        *mapping: "Mapping[Any, Program[V] | V] | Iterable[tuple[Any, Program[V] | V]]",
        **kwargs: "Program[V] | V",
    ) -> "Program[dict[Any, V]]":
        from doeff._collection_combinators import _dict

        return _dict(*mapping, **kwargs)


@dataclass
class _GenProgramThunk(ProgramBase[T]):
    """Program backed by a generator factory."""

    factory: "Callable[[], Generator[Effect | Program, Any, T]]"

    def to_generator(self) -> object:
        from doeff.do import make_doeff_generator

        return make_doeff_generator(self.factory())


@runtime_checkable
class ProgramProtocol(Protocol[T]):
    """
    Protocol for all executable computations in doeff.

    This protocol defines the core interface for effectful computations.
    """

    def map(self, f: Callable[[T], U]) -> "ProgramProtocol[U]":
        """Map a function over the result of this program (functor map)."""
        ...

    def flat_map(self, f: "Callable[[T], ProgramProtocol[U]]") -> "ProgramProtocol[U]":
        """Monadic bind operation - chain programs sequentially."""
        ...


Program = ProgramBase
GeneratorProgram = _GenProgramThunk


__all__ = [
    "DoCtrl",
    "DoExpr",
    "GeneratorProgram",
    "Program",
    "ProgramProtocol",
]
