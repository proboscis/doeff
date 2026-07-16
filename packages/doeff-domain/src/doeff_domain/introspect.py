"""handles annotation and two-layer handled-effects derivation (ADR-DOE-DOMAIN-001 D5/D6).

Layer 1 — annotation: ``handles(*effect_classes)`` sets ``__doeff_handles__``
on the target. This is the opt-in path for raw Python handlers/factories and
can be applied post-hoc to objects owned by other packages.

Layer 2 — structure: objects produced by doeff-hy's ``defhandler`` carry
``__doeff_body__``, the quoted clause list. The handled set is derived from
each clause's head symbol (= effect type name). Detection is attribute
duck-typing on purpose: doeff-domain must NOT import doeff-hy (D1). ``lazy``
clauses are skipped; ``:when``-guarded clauses count as participation
declarations, not totality guarantees.

Name resolution for clause heads: first the attributes of
``sys.modules[handler.__module__]``, then a name match against the caller's
``vocabulary`` (the domain's effect classes). If both fail, the derivation
raises — a clause that cannot be resolved must never be silently dropped.
"""

import sys
from collections.abc import Iterable

import hy
from doeff_vm import EffectBase

from doeff_domain.registry import DomainCheckError, DomainDefinitionError

HANDLES_ATTRIBUTE = "__doeff_handles__"
BODY_ATTRIBUTE = "__doeff_body__"
_LAZY_CLAUSE_HEAD = "lazy"


def handles(*effect_classes: type):
    """Annotate a raw handler (or handler factory) with its handled effect classes.

    Sets ``__doeff_handles__`` on the target and returns it — nothing else
    (D5). Usable as a decorator or applied post-hoc:
    ``handles(Ask, Local)(lazy_ask)``.
    """
    if not effect_classes:
        raise DomainDefinitionError("handles() requires at least one effect class")
    for effect in effect_classes:
        if not isinstance(effect, type) or not issubclass(effect, EffectBase):
            raise DomainDefinitionError(
                f"handles() arguments must be EffectBase subclasses, got {effect!r}"
            )

    def apply(handler):
        setattr(handler, HANDLES_ATTRIBUTE, tuple(effect_classes))
        return handler

    return apply


def handled_effects(handler, *, vocabulary: Iterable[type] = ()) -> frozenset[type]:
    """Derive the set of effect classes a handler declares to handle (D6).

    ``vocabulary`` is the fallback resolution context — normally the effects
    of the domain the handler is checked against.
    """
    if hasattr(handler, HANDLES_ATTRIBUTE):
        return _effects_from_annotation(handler)
    if hasattr(handler, BODY_ATTRIBUTE):
        return _effects_from_defhandler_body(handler, tuple(vocabulary))
    raise DomainCheckError(
        f"cannot determine handled effects for {_handler_label(handler)}: it has "
        f"neither a {HANDLES_ATTRIBUTE} annotation (use doeff_domain.handles(...)) "
        f"nor {BODY_ATTRIBUTE} clause data (defhandler product)"
    )


def _effects_from_annotation(handler) -> frozenset[type]:
    declared = handler.__doeff_handles__
    effects = []
    for effect in declared:
        if not isinstance(effect, type):
            raise DomainCheckError(
                f"{_handler_label(handler)}: {HANDLES_ATTRIBUTE} must contain "
                f"effect classes, got {effect!r}"
            )
        effects.append(effect)
    return frozenset(effects)


def _effects_from_defhandler_body(handler, vocabulary: tuple[type, ...]) -> frozenset[type]:
    effects: set[type] = set()
    for clause in handler.__doeff_body__:
        head = _clause_head(handler, clause)
        if head == _LAZY_CLAUSE_HEAD:
            continue
        effects.add(_resolve_effect_name(handler, head, vocabulary))
    return frozenset(effects)


def _clause_head(handler, clause) -> str:
    if isinstance(clause, str) or not hasattr(clause, "__getitem__"):
        raise DomainCheckError(
            f"{_handler_label(handler)}: malformed {BODY_ATTRIBUTE} clause "
            f"{clause!r} — expected an expression whose head is the effect type"
        )
    if len(clause) == 0:
        raise DomainCheckError(f"{_handler_label(handler)}: empty {BODY_ATTRIBUTE} clause")
    return str(clause[0])


def _resolve_effect_name(handler, head: str, vocabulary: tuple[type, ...]) -> type:
    module = sys.modules.get(handler.__module__)
    if module is not None:
        attribute = hy.mangle(head)
        if attribute in module.__dict__:
            candidate = module.__dict__[attribute]
            if isinstance(candidate, type):
                return candidate
    for effect in vocabulary:
        if isinstance(effect, type) and effect.__name__ == head:
            return effect
    raise DomainCheckError(
        f"{_handler_label(handler)}: cannot resolve effect type {head!r} from "
        f"defhandler clause — neither module {handler.__module__!r} attributes "
        f"nor the vocabulary ({[cls.__name__ for cls in vocabulary]}) resolve it "
        f"(ADR-DOE-DOMAIN-001 D6: unresolved clauses fail loud)"
    )


def _handler_label(handler) -> str:
    if hasattr(handler, "__doeff_name__"):
        return str(handler.__doeff_name__)
    if hasattr(handler, "__qualname__"):
        return str(handler.__qualname__)
    return repr(handler)
