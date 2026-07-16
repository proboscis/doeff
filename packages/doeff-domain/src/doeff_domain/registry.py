"""Domain data model and in-process registry (ADR-DOE-DOMAIN-001 D2/D3).

A Domain is pure data: the effect classes a cohesion unit *introduces*, the
vocabulary it *includes* by reference, canonical terms, the handlers expected
to cover it, and its laws. Registration happens at import time of the
declaring module; violations of the registry invariants raise immediately:

- same-name re-registration (D2),
- a second *introduction* of an effect class already introduced by another
  registered domain, keyed by class identity (D3 — includes are unlimited).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from doeff_vm import EffectBase


class DomainError(Exception):
    """Base class for doeff-domain errors."""


class DomainDefinitionError(DomainError):
    """A Domain/DomainTerm/DomainLaw was constructed with invalid data."""


class DomainRegistrationError(DomainError):
    """Registering a Domain violated a registry invariant."""


class DuplicateDomainNameError(DomainRegistrationError):
    """A domain with the same name is already registered (D2)."""


class DuplicateEffectIntroductionError(DomainRegistrationError):
    """An effect class was introduced by a second domain (D3)."""


class DomainCheckError(DomainError):
    """A conformance check could not be evaluated or found a stale declaration."""


class DomainCoverageError(DomainCheckError):
    """Check (a) failed: a domain's handlers do not cover its introduced effects."""


class OrphanEffectError(DomainCheckError):
    """Check (c) failed: an EffectBase subclass is not introduced by any domain."""


@dataclass(frozen=True)
class DomainTerm:
    """Canonical predicate/term declaration (declarative only in E1 — no check)."""

    name: str
    home: str
    description: str = ""

    def __post_init__(self) -> None:
        for attr in ("name", "home", "description"):
            value = getattr(self, attr)
            if not isinstance(value, str):
                raise DomainDefinitionError(f"DomainTerm.{attr} must be a string, got {value!r}")
        if not self.name:
            raise DomainDefinitionError("DomainTerm.name must be non-empty")


@dataclass(frozen=True)
class DomainLaw:
    """Domain law — doeff-adr's law is intentionally NOT reused (D1: no doeff-adr dependency)."""

    name: str
    statement: str
    counterexamples: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise DomainDefinitionError(
                f"DomainLaw.name must be a non-empty string, got {self.name!r}"
            )
        if not isinstance(self.statement, str) or not self.statement:
            raise DomainDefinitionError(
                f"DomainLaw.statement must be a non-empty string, got {self.statement!r}"
            )
        object.__setattr__(self, "counterexamples", tuple(self.counterexamples))
        for example in self.counterexamples:
            if not isinstance(example, str):
                raise DomainDefinitionError(
                    f"DomainLaw.counterexamples must be strings, got {example!r}"
                )


@dataclass(frozen=True)
class Domain:
    """A vocabulary cohesion unit as pure data (D2)."""

    name: str
    title: str
    effects: tuple[type, ...] = ()
    includes: tuple["Domain", ...] = ()
    terms: tuple[DomainTerm, ...] = ()
    handlers: tuple[Any, ...] = ()
    laws: tuple[DomainLaw, ...] = ()
    adrs: tuple[str, ...] = ()
    docs: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise DomainDefinitionError(
                f"Domain.name must be a non-empty string, got {self.name!r}"
            )
        if not isinstance(self.title, str) or not self.title:
            raise DomainDefinitionError(
                f"Domain.title must be a non-empty string, got {self.title!r}"
            )
        if not isinstance(self.docs, str):
            raise DomainDefinitionError(f"Domain.docs must be a string, got {self.docs!r}")
        for name in ("effects", "includes", "terms", "handlers", "laws", "adrs"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        self._validate_effects()
        self._validate_members()

    def _validate_effects(self) -> None:
        for effect in self.effects:
            if not isinstance(effect, type):
                # D2: real class references only — a string would keep "working"
                # after the import it names has broken.
                raise DomainDefinitionError(
                    f"Domain {self.name!r}: effects must be effect classes, "
                    f"got {effect!r} — string references are forbidden (D2)"
                )
            if not issubclass(effect, EffectBase):
                raise DomainDefinitionError(
                    f"Domain {self.name!r}: {effect.__qualname__} is not an EffectBase subclass"
                )
        seen: set[type] = set()
        for effect in self.effects:
            if effect in seen:
                raise DomainDefinitionError(
                    f"Domain {self.name!r}: effect {effect.__name__} listed twice in effects"
                )
            seen.add(effect)

    def _validate_members(self) -> None:
        for included in self.includes:
            if not isinstance(included, Domain):
                raise DomainDefinitionError(
                    f"Domain {self.name!r}: includes must be Domain references, got {included!r}"
                )
        for term in self.terms:
            if not isinstance(term, DomainTerm):
                raise DomainDefinitionError(
                    f"Domain {self.name!r}: terms must be DomainTerm values, got {term!r}"
                )
        for law in self.laws:
            if not isinstance(law, DomainLaw):
                raise DomainDefinitionError(
                    f"Domain {self.name!r}: laws must be DomainLaw values, got {law!r}"
                )
        for adr in self.adrs:
            if not isinstance(adr, str):
                raise DomainDefinitionError(
                    f"Domain {self.name!r}: adrs must be ADR id strings, got {adr!r}"
                )


_DOMAINS: dict[str, Domain] = {}
# Effect class (identity key) -> introducing domain name. Class identity, not
# class name: two distinct classes may share a __name__ (D3).
_EFFECT_HOME: dict[type, str] = {}


def register_domain(domain: Domain) -> Domain:
    """Register a Domain in the process registry; returns it.

    Validation is atomic: a failed registration leaves neither the domain
    name nor any partial effect introduction behind.
    """
    if not isinstance(domain, Domain):
        raise DomainRegistrationError(f"register_domain expects a Domain, got {domain!r}")
    if domain.name in _DOMAINS:
        raise DuplicateDomainNameError(
            f"domain {domain.name!r} is already registered — same-name "
            f"re-registration is forbidden (ADR-DOE-DOMAIN-001 D2)"
        )
    for effect in domain.effects:
        if effect in _EFFECT_HOME:
            raise DuplicateEffectIntroductionError(
                f"effect {effect.__name__} ({effect.__module__}) is already "
                f"introduced by domain {_EFFECT_HOME[effect]!r} and cannot also "
                f"be introduced by domain {domain.name!r} — introduce once, "
                f"include freely (ADR-DOE-DOMAIN-001 D3)"
            )
    _DOMAINS[domain.name] = domain
    for effect in domain.effects:
        _EFFECT_HOME[effect] = domain.name
    return domain


def get_domain(name: str) -> Domain:
    """Return the registered domain with the given name; loud KeyError otherwise."""
    if name not in _DOMAINS:
        raise KeyError(
            f"domain not registered: {name!r} (registered: {sorted(_DOMAINS) or 'none'})"
        )
    return _DOMAINS[name]


def registered_domains() -> tuple[Domain, ...]:
    """All registered domains, in registration order."""
    return tuple(_DOMAINS.values())


def domain_names() -> list[str]:
    return sorted(_DOMAINS)


def introducing_domain(effect: type) -> Domain:
    """Return the unique domain that introduces the given effect class."""
    if effect not in _EFFECT_HOME:
        raise KeyError(
            f"effect {effect.__name__} ({effect.__module__}) is not introduced "
            f"by any registered domain"
        )
    return _DOMAINS[_EFFECT_HOME[effect]]


def clear_registry() -> None:
    """Empty the registry. Test support only."""
    _DOMAINS.clear()
    _EFFECT_HOME.clear()


@contextmanager
def isolated_registry() -> Iterator[None]:
    """Run the body against a fresh, empty registry; restore the previous state on exit.

    Test support: lets red/green demonstrations register throwaway domains
    without colliding with import-time declarations (e.g. the dogfood module).
    """
    saved_domains = dict(_DOMAINS)
    saved_homes = dict(_EFFECT_HOME)
    clear_registry()
    try:
        yield
    finally:
        _DOMAINS.clear()
        _DOMAINS.update(saved_domains)
        _EFFECT_HOME.clear()
        _EFFECT_HOME.update(saved_homes)
