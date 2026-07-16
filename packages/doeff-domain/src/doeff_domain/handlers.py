"""Handler declarations and structural effect-set derivation."""

import sys
from collections.abc import Callable, Iterable, Sequence
from typing import Protocol, TypeVar, cast

from doeff_vm import EffectBase

from doeff_domain.registry import Domain

HandlerObject = TypeVar("HandlerObject")
_LAZY_CLAUSE_NAMES = frozenset({"lazy", "lazy-val", "lazy-var", "lazy_val", "lazy_var"})


class _HandlesDeclaration(Protocol):
    __doeff_handles__: Iterable[object]


class _DefhandlerDeclaration(Protocol):
    __doeff_body__: Iterable[Sequence[object]]


class _ModuleNamed(Protocol):
    __module__: str


def handles(
    *effect_classes: type[EffectBase],
) -> Callable[[HandlerObject], HandlerObject]:
    """Annotate a raw Python handler or factory with the effects it handles."""

    declared_effects = _validated_effect_classes(effect_classes, context="@handles")

    def annotate(handler: HandlerObject) -> HandlerObject:
        cast(_HandlesDeclaration, handler).__doeff_handles__ = declared_effects
        return handler

    return annotate


def handled_effects(
    handler: object,
    domain: Domain,
) -> frozenset[type[EffectBase]]:
    """Derive a handler's participating effect set using the two-layer protocol."""

    if hasattr(handler, "__doeff_handles__"):
        declaration = cast(_HandlesDeclaration, handler).__doeff_handles__
        return _validated_effect_classes(
            declaration,
            context=f"handler {_handler_name(handler)!r} __doeff_handles__",
        )
    if hasattr(handler, "__doeff_body__"):
        return _effects_from_defhandler_body(handler, domain)
    handler_name = _handler_name(handler)
    raise AssertionError(
        f"handler {handler_name!r} has neither a __doeff_handles__ declaration "
        "nor defhandler structure from which its effect set can be derived"
    )


def _validated_effect_classes(
    effect_classes: Iterable[object],
    *,
    context: str,
) -> frozenset[type[EffectBase]]:
    validated: set[type[EffectBase]] = set()
    for effect_class in effect_classes:
        if not isinstance(effect_class, type) or not issubclass(effect_class, EffectBase):
            raise TypeError(f"{context} must contain EffectBase classes, got {effect_class!r}")
        validated.add(effect_class)
    return frozenset(validated)


def _effects_from_defhandler_body(
    handler: object,
    domain: Domain,
) -> frozenset[type[EffectBase]]:
    body = cast(_DefhandlerDeclaration, handler).__doeff_body__
    try:
        clauses = tuple(body)
    except TypeError as error:
        raise AssertionError(
            f"handler {_handler_name(handler)!r} has invalid __doeff_body__: {body!r}"
        ) from error

    resolved_effects: set[type[EffectBase]] = set()
    for clause in clauses:
        try:
            if not clause:
                raise AssertionError(
                    f"handler {_handler_name(handler)!r} has an empty defhandler clause"
                )
            effect_name = str(clause[0])
        except (TypeError, IndexError) as error:
            raise AssertionError(
                f"handler {_handler_name(handler)!r} has invalid defhandler clause: {clause!r}"
            ) from error
        if effect_name in _LAZY_CLAUSE_NAMES:
            continue
        resolved_effects.add(_resolve_effect_name(handler, domain, effect_name))
    return frozenset(resolved_effects)


def _resolve_effect_name(
    handler: object,
    domain: Domain,
    effect_name: str,
) -> type[EffectBase]:
    handler_name = _handler_name(handler)
    if not hasattr(handler, "__module__"):
        raise AssertionError(
            f"effect {effect_name!r} in handler {handler_name!r} cannot be resolved: "
            "handler has no __module__"
        )
    module_name = cast(_ModuleNamed, handler).__module__
    module = sys.modules.get(module_name)
    if module is not None and effect_name in module.__dict__:
        candidate = module.__dict__[effect_name]
        if isinstance(candidate, type) and issubclass(candidate, EffectBase):
            return candidate

    matching_effects = tuple(
        effect_class for effect_class in domain.effects if effect_class.__name__ == effect_name
    )
    if len(matching_effects) == 1:
        return matching_effects[0]
    if len(matching_effects) > 1:
        qualified_names = ", ".join(
            f"{effect_class.__module__}.{effect_class.__qualname__}"
            for effect_class in matching_effects
        )
        raise AssertionError(
            f"effect {effect_name!r} in handler {handler_name!r} is ambiguous in domain "
            f"{domain.name!r}: {qualified_names}"
        )
    raise AssertionError(
        f"effect {effect_name!r} in handler {handler_name!r} cannot be resolved from "
        f"module {module_name!r} or domain {domain.name!r}"
    )


def _handler_name(handler: object) -> str:
    for attribute_name in ("__doeff_name__", "__qualname__", "__name__"):
        if hasattr(handler, attribute_name):
            return str(getattr(handler, attribute_name))
    return repr(handler)
