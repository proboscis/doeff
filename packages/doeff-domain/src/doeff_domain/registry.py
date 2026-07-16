"""Process-local vocabulary domain registry."""

from collections.abc import Sequence
from dataclasses import dataclass

from doeff_vm import EffectBase


@dataclass(frozen=True)
class DomainTerm:
    """A canonical predicate or term owned by a domain."""

    name: str
    home: str
    description: str


@dataclass(frozen=True)
class DomainLaw:
    """A domain-specific law and examples of violations."""

    name: str
    statement: str
    counterexamples: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "counterexamples", tuple(self.counterexamples))


@dataclass(frozen=True)
class Domain:
    """Pure declaration data for one cohesive effect vocabulary."""

    name: str
    title: str
    effects: Sequence[type[EffectBase]] = ()
    includes: Sequence["Domain"] = ()
    terms: Sequence[DomainTerm] = ()
    handlers: Sequence[object] = ()
    laws: Sequence[DomainLaw] = ()
    adrs: Sequence[str] = ()
    docs: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "effects", tuple(self.effects))
        object.__setattr__(self, "includes", tuple(self.includes))
        object.__setattr__(self, "terms", tuple(self.terms))
        object.__setattr__(self, "handlers", tuple(self.handlers))
        object.__setattr__(self, "laws", tuple(self.laws))
        object.__setattr__(self, "adrs", tuple(self.adrs))
        object.__setattr__(self, "docs", tuple(self.docs))


_DOMAINS_BY_NAME: dict[str, Domain] = {}
_EFFECT_OWNERS: dict[type[EffectBase], Domain] = {}


def register_domain(domain: Domain) -> Domain:
    """Register a domain after atomically validating all registry invariants."""

    _validate_domain_shape(domain)
    if domain.name in _DOMAINS_BY_NAME:
        raise ValueError(f"duplicate domain name: {domain.name}")

    seen_effects: set[type[EffectBase]] = set()
    for effect_class in domain.effects:
        if effect_class in seen_effects:
            raise ValueError(
                f"domain {domain.name!r} introduces effect {effect_class.__name__} more than once"
            )
        seen_effects.add(effect_class)
        existing_owner = _EFFECT_OWNERS.get(effect_class)
        if existing_owner is not None:
            raise ValueError(
                f"effect {effect_class.__module__}.{effect_class.__qualname__} is already "
                f"introduced by domain {existing_owner.name!r}; domain {domain.name!r} "
                "cannot introduce it again"
            )

    _DOMAINS_BY_NAME[domain.name] = domain
    for effect_class in domain.effects:
        _EFFECT_OWNERS[effect_class] = domain
    return domain


def _validate_domain_shape(domain: Domain) -> None:
    if not isinstance(domain, Domain):
        raise TypeError(f"register_domain requires Domain, got {type(domain).__name__}")
    if not domain.name:
        raise ValueError("domain name must not be empty")
    if not domain.title:
        raise ValueError(f"domain {domain.name!r} title must not be empty")
    for effect_class in domain.effects:
        if not isinstance(effect_class, type) or not issubclass(effect_class, EffectBase):
            raise TypeError(
                f"domain {domain.name!r} effect must be an EffectBase class, got {effect_class!r}"
            )
    for included_domain in domain.includes:
        if not isinstance(included_domain, Domain):
            raise TypeError(
                f"domain {domain.name!r} includes must contain Domain values, "
                f"got {included_domain!r}"
            )
    for term in domain.terms:
        if not isinstance(term, DomainTerm):
            raise TypeError(
                f"domain {domain.name!r} terms must contain DomainTerm values, got {term!r}"
            )
    for law in domain.laws:
        if not isinstance(law, DomainLaw):
            raise TypeError(
                f"domain {domain.name!r} laws must contain DomainLaw values, got {law!r}"
            )


def registered_domains() -> list[Domain]:
    """Return registered domains in declaration order."""

    return list(_DOMAINS_BY_NAME.values())


def get_domain(name: str) -> Domain:
    """Return a registered domain by its unique name."""

    return _DOMAINS_BY_NAME[name]


def domain_for_effect(effect_class: type[EffectBase]) -> Domain | None:
    """Return the domain that introduces an effect class, if registered."""

    return _EFFECT_OWNERS.get(effect_class)


def clear_registry() -> None:
    """Clear process-local declarations, primarily for isolated test processes."""

    _DOMAINS_BY_NAME.clear()
    _EFFECT_OWNERS.clear()
