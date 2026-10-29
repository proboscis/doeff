"""Handles declarations and two-layer handled-effects derivation (ADR-DOE-DOMAIN-001 D5/D6).

Layer 1 — declaration: a domain lists a raw Python handler/factory as
``Handles(handler, *effect_classes)`` in its ``handlers``. This is the opt-in
path for raw handlers, and it declares handlers owned by other packages
without touching them. A handler that carries its own ``__doeff_handles__``
mark (the core's installers set it) is read from that mark. The function
``handles()`` that set the mark post-hoc on the target was made this type
(ADR-DOE-API-001 R24 — card ki-03a7f0af2b64: neither a handler factory (R3)
nor a program helper (R18), so it became a value, as R19 did for check).

Layer 2 — structure: objects produced by doeff-hy's ``defhandler`` carry
``__doeff_body__``, the quoted clause list. The handled set is derived from
each clause's head symbol (= effect type name). Detection is attribute
duck-typing on purpose: doeff-domain must NOT import doeff-hy (D1). Session-value
clauses — ``(session val …)`` / ``(session var …)`` (ADR-DOE-HY-006) and the
legacy lazy-init heads ``lazy`` / ``lazy-val`` / ``lazy-var`` — are skipped;
``:when``-guarded clauses count as participation declarations, not totality
guarantees.

Name resolution for clause heads: first the attributes of
``sys.modules[handler.__module__]``, then a name match against the caller's
``vocabulary`` (the domain's effect classes). If both fail, the derivation
raises — a clause that cannot be resolved must never be silently dropped.
"""

import sys
from collections.abc import Iterable
from dataclasses import dataclass

import hy

from doeff import EffectBase
from doeff_domain._registry import DomainCheckError, DomainDefinitionError

HANDLES_ATTRIBUTE = "__doeff_handles__"
BODY_ATTRIBUTE = "__doeff_body__"
# defhandler の節のうち effect を処理しない、session の値の宣言の head
# (doeff-hy handle.hy の _extract-lazy-clauses)。session = (session val|var 名 式)
# (ADR-DOE-HY-006)、lazy / lazy-val / lazy-var = 旧い形(_is-lazy-clause)。
# quoted body は書かれたままの Symbol を保持するが、mangle 済み表記も安全側で含める。
_LAZY_CLAUSE_HEADS = frozenset({"session", "lazy", "lazy-val", "lazy-var", "lazy_val", "lazy_var"})


@dataclass(frozen=True, init=False)
class Handles:
    """A domain's handler entry that declares the effect classes the handler answers (D5).

    For raw Python handlers and factories, whose clauses cannot be read: the
    domain lists ``Handles(lazy_ask, Ask, Local)`` in its ``handlers``. The
    declaration lives in the domain, so a handler owned by another package is
    declared without setting anything on it. A participation declaration, not
    a totality guarantee.
    """

    handler: object
    effects: tuple[type, ...]

    def __init__(self, handler: object, *effects: type) -> None:
        if not effects:
            raise DomainDefinitionError("Handles(handler, …) requires at least one effect class")
        for effect in effects:
            if not isinstance(effect, type) or not issubclass(effect, EffectBase):
                raise DomainDefinitionError(
                    f"Handles(handler, …) effects must be EffectBase subclasses, got {effect!r}"
                )
        object.__setattr__(self, "handler", handler)
        object.__setattr__(self, "effects", tuple(effects))


def handled_effects(handler, *, vocabulary: Iterable[type] = ()) -> frozenset[type]:
    """Derive the set of effect classes a domain's handler entry declares to handle (D6).

    The answer of the effect HandledEffects. ``vocabulary`` is the fallback
    resolution context — normally the effects of the domain the handler is
    checked against.
    """
    if isinstance(handler, Handles):
        return frozenset(handler.effects)
    if hasattr(handler, HANDLES_ATTRIBUTE):
        return _effects_from_annotation(handler)
    if hasattr(handler, BODY_ATTRIBUTE):
        return _effects_from_defhandler_body(handler, tuple(vocabulary))
    raise DomainCheckError(
        f"cannot determine handled effects for {_handler_label(handler)}: it is not "
        f"declared with doeff_domain.Handles(handler, …) and has neither a "
        f"{HANDLES_ATTRIBUTE} mark nor {BODY_ATTRIBUTE} clause data (defhandler product)"
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
        if head in _LAZY_CLAUSE_HEADS:
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
