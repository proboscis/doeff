"""The effect types a handler declares on its effect parameter.

A handler is any callable ``(effect, k) -> Program``. When its effect parameter
carries a type annotation narrower than "every effect", the VM skips the
handler for effects that are not instances of that type — exactly as if the
handler had started with ``if not isinstance(effect, T): yield Pass(effect, k)``
(SPEC-WITHHANDLER-TYPE-FILTER; SPEC-VM-020 "pattern-match after dispatch").

    @do
    def clock(effect: ReadClock | Sleep, k): ...      # sees ReadClock and Sleep only
    @do
    def guard(effect: WriteEffect, k): ...            # sees every WriteEffect subclass
    @do
    def logger(effect: EffectBase, k): ...            # sees everything (no filter)

This module is the single place that turns an annotation into that type tuple.
The VM calls ``handler_effect_types`` once per ``WithHandler`` it installs; the
answer is cached on the underlying function, so a handler that is installed
many times (Spawn re-installs the handler stack in every task) pays the
annotation read once. A handler made afresh for every example (a closure
returned by a handler factory) is a new function each time, so the resolved
types are also shared by the function's code, keyed by the annotation value:
closures of one code whose annotations are equal resolve once, and closures
whose annotation reads a closure variable with a different value resolve
their own. Only the annotation resolution is shared — the passed effects
(``__doeff_passes__``), the generator function and the tail-resume lines are
read from each function, because they can differ between closures.

Resolution rules (``None`` = no filter = the handler sees every effect):

- the effect parameter is the first positional parameter that is not already
  bound: ``functools.partial`` positional arguments and the ``self`` of a bound
  method or callable instance are skipped; ``@do`` / ``functools.wraps``
  wrappers are followed through ``__wrapped__``
- no annotation, ``Any``, ``object``, ``EffectBase`` (alias ``Effect``) → None
- a class ``T`` → ``(T,)`` — ``isinstance`` semantics, so a parent class (a
  "marker" base such as ``WriteEffect``) covers all of its subclasses,
  including ones defined later
- ``A | B`` / ``Union[A, B]`` → ``(A, B)``; if any member means "everything",
  the union means "everything"
- a parameterised generic ``WriteEffect[bool]`` → its class ``WriteEffect``
- ``Annotated[T, ...]`` → the rule for ``T``
- a string annotation (``from __future__ import annotations``) is evaluated in
  the function's module globals
- anything else (TypeVar, Literal, a non-runtime-checkable Protocol, an
  annotation that cannot be evaluated) → None, i.e. today's behaviour of
  delivering every effect. An annotation that cannot be evaluated also emits a
  ``RuntimeWarning`` once per function, because the author asked for a filter
  that the VM could not apply.
"""

import functools
import types
import typing
import warnings
import weakref
from typing import Annotated, Any, NamedTuple, Union, get_args, get_origin

from doeff_vm.doeff_vm import EffectBase, HandlerSpec

EffectTypes = tuple[type, ...] | None

_CACHE_ATTR = "__doeff_effect_types__"
_NOT_ANNOTATED = object()
_UNRESOLVED = object()
_CATCH_ALL = (typing.Any, object, EffectBase)


class PassedEffects(NamedTuple):
    """Effects a catch-all handler passes on untouched: an instance of ``passes``
    that is not an instance of ``keeps``.

    A handler whose one catch-all clause is guarded by
    ``(not (isinstance effect X))`` answers nothing for an ``X`` effect that no
    earlier clause names — it passes it on first thing. ``passes`` is ``X`` and
    ``keeps`` the types of the clauses before the catch-all (the handler still
    sees those, whatever their guards say). The VM skips the handler for such
    an effect without calling into Python — the complement of the annotation
    filter, which cannot say "everything except X" (agora-redesign #2008).
    """

    passes: tuple[type, ...]
    keeps: tuple[type, ...]


# Where handler_spec keeps a plain function's HandlerSpec. HandlerSpec (effect_types,
# generator_function, tail_resume_lines, passed) is the VM's own type, so the VM reads
# the kept spec here in place, without calling handler_spec (agora-redesign #2927).
_SPEC_ATTR = "__doeff_handler_spec__"
_PASSES_ATTR = "__doeff_passes__"


def declare_passes(handler: types.FunctionType, source: typing.Callable[[], tuple[object, object]]) -> types.FunctionType:
    """Record on ``handler`` where its passed effects come from; returns ``handler``.

    The defhandler / handle expansion calls this with a thunk answering
    ``(X, (T1, ...))`` — the guard's type and the earlier clauses' types. The
    thunk runs once, when the VM first installs the handler (the guard's ``X``
    is a handler parameter or a module constant, bound once), so a name defined
    after the handler still resolves.
    """
    setattr(handler, _PASSES_ATTR, source)
    return handler


def _passed_effects(handler: types.FunctionType) -> PassedEffects | None:
    """The effects the VM may skip this handler for — so a catch-all guard does not cost a Python call per effect."""
    source = handler.__dict__.get(_PASSES_ATTR)
    if source is None:
        return None
    try:
        passes, keeps = source()
    except (NameError, AttributeError):
        # 番の型が install の時に解けない — 今までどおり全部の effect を handler が受ける(番がその場で判じる)
        return None
    passes = passes if isinstance(passes, tuple) else (passes,)
    if not passes or not all(isinstance(t, type) for t in passes + tuple(keeps)):
        return None
    return PassedEffects(passes, tuple(keeps))


def handler_spec(handler: object) -> HandlerSpec:
    """The VM's install-time view of a handler; cached on plain functions."""
    if type(handler) is types.FunctionType:
        cached = handler.__dict__.get(_SPEC_ATTR)
        if cached is not None:
            return cached
        spec = HandlerSpec(
            handler_effect_types(handler),
            handler.__dict__.get("__doeff_generator_function__"),
            tuple(handler.__dict__.get("__doeff_tail_resume_lines__", ())),
            _passed_effects(handler),
        )
        setattr(handler, _SPEC_ATTR, spec)
        return spec
    if isinstance(handler, types.MethodType):
        definition = handler.__func__.__dict__
        generator = definition.get("__doeff_generator_function__")
        if isinstance(generator, types.FunctionType):
            # @do の定義を同じ self / cls に束縛する。型の解決は元の
            # bound method に対して行い、effect の前の self を読み飛ばす。
            # self を関数の cache に保存しない: 別 instance と共有しない。
            return HandlerSpec(
                handler_effect_types(handler),
                types.MethodType(generator, handler.__self__),
                tuple(definition.get("__doeff_tail_resume_lines__", ())),
                None,
            )
    return HandlerSpec(handler_effect_types(handler), None, (), None)


class _EffectParameter(NamedTuple):
    function: types.FunctionType | None
    position: int


def handler_effect_types(handler: object) -> EffectTypes:
    """Types the handler's effect parameter admits, or None for every effect."""
    function, position = _effect_parameter(handler)
    if function is None:
        return None
    return _cached_parameter_types(function, position)


def _effect_parameter(handler: object) -> _EffectParameter:
    index = 0
    current = handler
    while True:
        if isinstance(current, functools.partial):
            index += len(current.args)
            current = current.func
            continue
        if isinstance(current, types.MethodType):
            index += 1
            current = current.__func__
            continue
        if isinstance(current, types.FunctionType):
            wrapped = current.__dict__.get("__wrapped__")
            if wrapped is not None:
                current = wrapped
                continue
            return _EffectParameter(current, index)
        if isinstance(current, type) or not callable(current):
            return _EffectParameter(None, index)
        call = type(current).__call__
        if not isinstance(call, types.FunctionType):
            return _EffectParameter(None, index)
        index += 1
        current = call


def _cached_parameter_types(function: types.FunctionType, index: int) -> EffectTypes:
    cache = function.__dict__.get(_CACHE_ATTR)
    if cache is None:
        cache = {}
        setattr(function, _CACHE_ATTR, cache)
    elif index in cache:
        return cache[index]
    result = _parameter_types(function, index)
    cache[index] = result
    return result


def _parameter_types(function: types.FunctionType, index: int) -> EffectTypes:
    code = function.__code__
    if index >= code.co_argcount:
        return None
    name = code.co_varnames[index]
    annotation = _parameter_annotation(function, name)
    if annotation is _NOT_ANNOTATED:
        return None
    if annotation is _UNRESOLVED:
        _warn_unresolved(function, name)
        return None
    if isinstance(annotation, str):
        # 文字の註は関数の globals で評価する — code が同じでも globals が違えば答えが違いうるので code で共有しない
        return _resolved_or_warn(annotation, function, name)
    return _shared_parameter_types(function, index, name, annotation)


class _SharedResolution(NamedTuple):
    """One code's resolved effect parameter, shared by the closures made from that code.

    ``code`` (weak) and ``index`` say which code and parameter it is for.
    ``annotation`` is the annotation value the types were resolved from. A
    closure reuses ``effect_types`` only when its own annotation value is the
    same (``_same_annotation``) — an annotation that reads a closure variable
    (evaluated per closure) gives each closure its own value, so two closures
    of one code that admit different types never share an answer.
    """

    code: "weakref.ref[types.CodeType]"
    index: int
    annotation: object
    effect_types: EffectTypes


# id(code) → the latest resolution for that code. One entry per function definition in
# the source (not one per closure): a closure whose annotation or parameter differs
# replaces the entry, and the entry goes when its code is collected (the weak
# reference's callback). Keyed by id, not by the code: hashing a code object reads
# its whole contents, and a WeakKeyDictionary lookup builds a weak reference per call.
_SHARED_BY_CODE: dict[int, _SharedResolution] = {}


def _shared_parameter_types(function: types.FunctionType, index: int, name: str, annotation: object) -> EffectTypes:
    """Resolve once per code — a handler made afresh for every example re-resolves nothing (agora-redesign #2422)."""
    code = function.__code__
    shared = _SHARED_BY_CODE.get(id(code))
    if (
        shared is not None
        and shared.code() is code
        and shared.index == index
        and _same_annotation(shared.annotation, annotation)
    ):
        return shared.effect_types
    resolved = _types_of(annotation, function)
    if resolved is _UNRESOLVED:
        # 解けない註は共有しない — 後で作る閉包では解けうる(後で定義した名前)
        _warn_unresolved(function, name)
        return None
    key = id(code)
    _SHARED_BY_CODE[key] = _SharedResolution(weakref.ref(code, functools.partial(_forget_code, key)), index, annotation, resolved)
    return resolved


def _forget_code(key: int, code: "weakref.ref[types.CodeType]") -> None:
    """Drop a collected code's entry, so the shared resolutions never outlive their code (and an id reused later starts clean)."""
    shared = _SHARED_BY_CODE.get(key)
    if shared is not None and shared.code is code:
        del _SHARED_BY_CODE[key]


def _same_annotation(shared: object, current: object) -> bool:
    """The same annotation value: the same object, or an equal one of the same kind with the same members in order.

    ``A | B`` is a new object each time it is evaluated, so identity alone would
    never share a union; its members (classes) compare by identity in order.
    """
    if shared is current:
        return True
    return type(shared) is type(current) and shared == current and get_args(shared) == get_args(current)


def _resolved_or_warn(annotation: object, function: types.FunctionType, name: str) -> EffectTypes:
    """Resolve a text annotation for this function alone — an unresolvable one leaves the handler unfiltered, with a warning."""
    resolved = _types_of(annotation, function)
    if resolved is _UNRESOLVED:
        _warn_unresolved(function, name)
        return None
    return resolved


def _parameter_annotation(function: types.FunctionType, name: str) -> object:
    try:
        annotations = function.__annotations__
    except (NameError, AttributeError):
        # Python 3.14 evaluates annotations lazily; a name that exists only
        # under TYPE_CHECKING fails here.
        return _UNRESOLVED
    return annotations.get(name, _NOT_ANNOTATED)


def _types_of(annotation: object, function: types.FunctionType) -> Any:
    if isinstance(annotation, str):
        evaluated = _evaluate(annotation, function)
        return _UNRESOLVED if evaluated is _UNRESOLVED else _types_of(evaluated, function)
    if any(annotation is catch_all for catch_all in _CATCH_ALL):
        return None
    origin = get_origin(annotation)
    if origin is Annotated:
        return _types_of(get_args(annotation)[0], function)
    if origin is Union or origin is types.UnionType:
        return _union_types(get_args(annotation), function)
    cls = origin if isinstance(origin, type) else annotation
    return _class_filter(cls) if isinstance(cls, type) else None


def _evaluate(text: str, function: types.FunctionType) -> object:
    try:
        return eval(text, function.__globals__)  # annotation text of the handler's own module
    except (NameError, AttributeError, SyntaxError, TypeError):
        return _UNRESOLVED


def _union_types(members: tuple[object, ...], function: types.FunctionType) -> Any:
    collected: list[type] = []
    for member in members:
        resolved = _types_of(member, function)
        if resolved is None or resolved is _UNRESOLVED:
            return resolved
        collected.extend(resolved)
    return tuple(collected)


def _class_filter(cls: type) -> EffectTypes:
    if cls.__dict__.get("_is_protocol") is True and cls.__dict__.get("_is_runtime_protocol") is not True:
        # isinstance() against a plain Protocol raises TypeError.
        return None
    return (cls,)


def _warn_unresolved(function: types.FunctionType, name: str) -> None:
    warnings.warn_explicit(
        f"handler {function.__qualname__}: the annotation of effect parameter {name!r} "
        "cannot be evaluated at runtime, so the handler receives every effect "
        "(no type filter). Import the effect type at runtime to get the filter.",
        RuntimeWarning,
        function.__code__.co_filename,
        function.__code__.co_firstlineno,
    )


# --- runtime readers of the static types (foundation for the Hy checker) -----------------------
#
# The static types live in ordinary annotations: ``class ReadClock(EffectBase[int])`` and
# ``def f(...) -> Generator[E, Any, T]`` under ``@do``. These readers expose the same facts at
# runtime so tools that cannot run pyright (the doeff-hy static checker, coverage checks of
# effects against handler stacks) read one source of truth instead of re-declaring types.


def effect_result_type(effect: object) -> object:
    """The ``T`` of ``EffectBase[T]`` for an effect class or instance.

    Follows generic parents: for ``class WriteEffect(EffectBase[T])`` and
    ``class WriteShared(WriteEffect[bool])`` it returns ``bool``. An effect that
    never parameterises ``EffectBase`` returns ``typing.Any``.
    """
    cls = effect if isinstance(effect, type) else type(effect)
    found = _result_from(cls, {})
    return typing.Any if found is _UNRESOLVED else found


def _result_from(cls: type, substitution: dict[object, object]) -> object:
    for base in cls.__dict__.get("__orig_bases__", ()):
        origin = get_origin(base)
        if origin is None:
            continue
        args = tuple(substitution.get(arg, arg) for arg in get_args(base))
        if origin is EffectBase:
            return args[0] if args else typing.Any
        if isinstance(origin, type) and issubclass(origin, EffectBase):
            parameters = _type_parameters(origin)
            found = _result_from(origin, dict(zip(parameters, args, strict=False)))
            if found is not _UNRESOLVED:
                return found
    for base in cls.__bases__:
        if isinstance(base, type) and issubclass(base, EffectBase) and base is not EffectBase:
            found = _result_from(base, substitution)
            if found is not _UNRESOLVED:
                return found
    return _UNRESOLVED


def _type_parameters(cls: type) -> list[object]:
    declared = cls.__dict__.get("__parameters__")
    if declared:
        return list(declared)
    collected: list[object] = []
    for base in cls.__dict__.get("__orig_bases__", ()):
        for arg in get_args(base):
            if isinstance(arg, typing.TypeVar) and arg not in collected:
                collected.append(arg)
    return collected


class ProgramSignature(NamedTuple):
    """What a ``@do`` function's annotation declares.

    ``effects``: the effect types its body may yield (the ``E`` of
    ``Generator[E, Any, T]``, a union flattened into a tuple), or ``None`` when
    the annotation leaves them open (no annotation, ``Any``, ``EffectGenerator[T]``).
    ``result``: the ``T`` (``typing.Any`` when not declared).
    """

    effects: tuple[object, ...] | None
    result: object


def program_signature(function: object) -> ProgramSignature:
    """Read ``Generator[E, Any, T]`` from a ``@do`` function (or the raw generator function)."""
    current = function
    while isinstance(current, types.FunctionType) and current.__dict__.get("__wrapped__") is not None:
        current = current.__dict__["__wrapped__"]
    if not isinstance(current, types.FunctionType):
        return ProgramSignature(None, typing.Any)
    annotation = _parameter_annotation(current, "return")
    if isinstance(annotation, str):
        annotation = _evaluate(annotation, current)
    if annotation is _NOT_ANNOTATED or annotation is _UNRESOLVED:
        return ProgramSignature(None, typing.Any)
    args = get_args(annotation)
    if len(args) != 3:
        return ProgramSignature(None, typing.Any)
    yielded, _sent, result = args
    if yielded is typing.Any:
        return ProgramSignature(None, result)
    origin = get_origin(yielded)
    effects = get_args(yielded) if origin is Union or origin is types.UnionType else (yielded,)
    return ProgramSignature(tuple(effects), result)
