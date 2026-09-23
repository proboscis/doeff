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
annotation read once.

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
from typing import Annotated, Any, NamedTuple, Union, get_args, get_origin

from doeff_vm.doeff_vm import EffectBase

EffectTypes = tuple[type, ...] | None

_CACHE_ATTR = "__doeff_effect_types__"
_NOT_ANNOTATED = object()
_UNRESOLVED = object()
_CATCH_ALL = (typing.Any, object, EffectBase)


class _EffectParameter(NamedTuple):
    function: types.FunctionType | None
    index: int


def handler_effect_types(handler: object) -> EffectTypes:
    """Types the handler's effect parameter admits, or None for every effect."""
    parameter = _effect_parameter(handler)
    if parameter.function is None:
        return None
    return _cached_parameter_types(parameter.function, parameter.index)


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
