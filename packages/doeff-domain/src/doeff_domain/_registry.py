"""Domain data model and the domain tables (ADR-DOE-DOMAIN-001 D2/D3).

A Domain is pure data: the effect classes a cohesion unit *introduces*, the
vocabulary it *includes* by reference, canonical terms, the handlers expected
to cover it, and its laws. ``defdomain`` registers into the process table at
import time of the declaring module; a table given to ``domain_registry``
(doeff_domain._effects) is a separate table with the same invariants.
Violations of the table invariants raise immediately:

- same-name re-registration (D2),
- a second *introduction* of an effect class already introduced by another
  registered domain, keyed by class identity (D3 — includes are unlimited).

Inside the package: programs read and check the tables through the effects of
doeff_domain._effects (ADR-DOE-API-001 R23 — card ki-03a7f0af2b64).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from doeff import EffectBase


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


class DomainTable:
    """One table of registered domains: name -> Domain and effect class -> introducing domain name.

    The process table (``PROCESS_TABLE``) is the one ``defdomain`` registers into; ``domain_registry``
    answers from it, or from a table of its own when given ``domains``.
    """

    def __init__(self) -> None:
        self._domains: dict[str, Domain] = {}
        # Effect class (identity key) -> introducing domain name. Class identity, not
        # class name: two distinct classes may share a __name__ (D3).
        self._effect_home: dict[type, str] = {}

    def register(self, domain: Domain) -> Domain:
        """Register a Domain in this table; returns it.

        Validation is atomic: a failed registration leaves neither the domain
        name nor any partial effect introduction behind.
        """
        if not isinstance(domain, Domain):
            raise DomainRegistrationError(f"register_domain expects a Domain, got {domain!r}")
        if domain.name in self._domains:
            raise DuplicateDomainNameError(
                f"domain {domain.name!r} is already registered — same-name "
                f"re-registration is forbidden (ADR-DOE-DOMAIN-001 D2)"
            )
        for effect in domain.effects:
            if effect in self._effect_home:
                raise DuplicateEffectIntroductionError(
                    f"effect {effect.__name__} ({effect.__module__}) is already "
                    f"introduced by domain {self._effect_home[effect]!r} and cannot also "
                    f"be introduced by domain {domain.name!r} — introduce once, "
                    f"include freely (ADR-DOE-DOMAIN-001 D3)"
                )
        self._domains[domain.name] = domain
        for effect in domain.effects:
            self._effect_home[effect] = domain.name
        return domain

    def get(self, name: str) -> Domain:
        """Return the registered domain with the given name; loud KeyError otherwise."""
        if name not in self._domains:
            raise KeyError(
                f"domain not registered: {name!r} (registered: {sorted(self._domains) or 'none'})"
            )
        return self._domains[name]

    def domains(self) -> list[Domain]:
        """All registered domains, in registration order."""
        return list(self._domains.values())

    def names(self) -> list[str]:
        """The registered names, sorted — what a missing-name message lists."""
        return sorted(self._domains)

    def introduces(self, effect: type) -> bool:
        """Whether some domain of the table introduces the effect class (what check (c) asks)."""
        return effect in self._effect_home

    def introducing(self, effect: type) -> Domain:
        """Return the unique domain that introduces the given effect class."""
        if effect not in self._effect_home:
            raise KeyError(
                f"effect {effect.__name__} ({effect.__module__}) is not introduced "
                f"by any registered domain"
            )
        return self._domains[self._effect_home[effect]]

    def clear(self) -> None:
        """Empty the table (test support — the process table between demonstrations)."""
        self._domains.clear()
        self._effect_home.clear()

    @contextmanager
    def isolated(self) -> Iterator[None]:
        """Run the body against this table emptied; restore its previous state on exit."""
        saved_domains = dict(self._domains)
        saved_homes = dict(self._effect_home)
        self.clear()
        try:
            yield
        finally:
            self._domains.clear()
            self._domains.update(saved_domains)
            self._effect_home.clear()
            self._effect_home.update(saved_homes)


PROCESS_TABLE = DomainTable()


def register_domain(domain: Domain) -> Domain:
    """Register a Domain in the process table; returns it (the expansion of defdomain)."""
    return PROCESS_TABLE.register(domain)


def get_domain(name: str) -> Domain:
    """The process table's domain with the given name; loud KeyError otherwise."""
    return PROCESS_TABLE.get(name)


def registered_domains() -> list[Domain]:
    """All domains of the process table, in registration order."""
    return PROCESS_TABLE.domains()


def domain_names() -> list[str]:
    """The process table's names, sorted (for reading what is declared in this process)."""
    return PROCESS_TABLE.names()


def introducing_domain(effect: type) -> Domain:
    """The process table's domain that introduces the given effect class."""
    return PROCESS_TABLE.introducing(effect)


def clear_registry() -> None:
    """Empty the process table. Test support only."""
    PROCESS_TABLE.clear()


@contextmanager
def isolated_registry() -> Iterator[None]:
    """Run the body against a fresh, empty process table; restore the previous state on exit.

    Test support for the declarations that register at import or eval time (defdomain):
    lets red/green demonstrations register throwaway domains without colliding with
    import-time declarations (e.g. the dogfood module). A program swaps the table
    with ``domain_registry(domains=[…])`` instead.
    """
    with PROCESS_TABLE.isolated():
        yield
