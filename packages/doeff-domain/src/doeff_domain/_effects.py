"""The effects that put, get, and check domains (groups of vocabulary) in a table, and domain_registry, the factory returning the handler that answers from the table.

Decision: make them effects (
ADR-DOE-API-001 R29). The 8 former functions (register_domain, isolated_registry, get_domain, handles, handled_effects,
assert_domain_covered, assert_registered_domains_covered, assert_no_orphan_effects) were removed from the public names -- the bodies live in the inner
_registry, _checks and _introspect, and the handler answers with them. isolated_registry is replaced by domain_registry(domains=[]),
and handles became the declaration type Handles (see the docstring of _introspect).

The declaration defdomain still registers into the process table at import time. A program reads the table via effects; domain_registry() reads the process table
when answering, and domain_registry(domains=[domain, ...]) answers from a separate table holding only the given domains (RegisterDomain also
registers into that table and does not fall through to the process table). The innermost one answers first, so a test can run only part of a program against a separate table.
A check failure is a child of DomainError (a module that cannot be read during the scan is also wrapped in DomainCheckError -- the original exception is __cause__); a name not in the table is a
KeyError; both are thrown back to the place that raised the effect.
"""

from dataclasses import dataclass

from doeff import EffectBase, K, Transfer, TransferThrow, handler
from doeff.program import ProgramHandler
from doeff_domain._checks import (
    assert_domain_covered,
    assert_no_orphan_effects,
    assert_registered_domains_covered,
)
from doeff_domain._introspect import handled_effects
from doeff_domain._registry import PROCESS_TABLE, Domain, DomainError, DomainTable


def _types(values: object, field: str) -> tuple[type, ...]:
    """Normalize the sequence of classes passed to an effect field into a tuple (so a list also works -- a wrong shape is rejected at construction)."""
    if not isinstance(values, list | tuple):
        raise TypeError(f"{field} takes a list of effect classes, got {type(values).__name__}")
    return tuple(values)


@dataclass(frozen=True)
class RegisterDomain(EffectBase[Domain]):
    """Register a domain in the table: yield RegisterDomain(domain) -> the registered Domain. A duplicate name or a second introduction is a child of DomainRegistrationError."""

    domain: Domain


@dataclass(frozen=True)
class GetDomain(EffectBase[Domain]):
    """Look up a domain by name: yield GetDomain("doeff-reader") -> Domain. A name that is not registered raises KeyError."""

    name: str


@dataclass(frozen=True)
class HandledEffects(EffectBase[frozenset[type]]):
    """Read the classes of the effects that one of the domain's handlers answers: yield HandledEffects(handler, vocabulary=(...)) -> frozenset.

    handler is one of the domain's handlers -- a Handles(handler, ...) declaration, a handler with a __doeff_handles__ mark, or a defhandler with clauses. If it cannot be read:
    DomainCheckError. vocabulary is a fallback for resolving clause head names (normally the effects of the domain being checked).
    """

    handler: object
    vocabulary: tuple[type, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "vocabulary", _types(self.vocabulary, "HandledEffects.vocabulary"))


@dataclass(frozen=True)
class AssertDomainCovered(EffectBase[None]):
    """check (a): the domain's handlers answer every effect the domain introduces. A violation is DomainCoverageError.

    domain is a Domain or a name looked up from the table. known_uncovered is a declared gap (if it is no longer a gap, a stale DomainCheckError).
    """

    domain: Domain | str
    known_uncovered: tuple[type, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "known_uncovered",
            _types(self.known_uncovered, "AssertDomainCovered.known_uncovered"),
        )


@dataclass(frozen=True)
class AssertRegisteredDomainsCovered(EffectBase[None]):
    """Apply check (a) to every domain in the table. A violation is DomainCoverageError."""

    known_uncovered: tuple[type, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "known_uncovered",
            _types(self.known_uncovered, "AssertRegisteredDomainsCovered.known_uncovered"),
        )


@dataclass(frozen=True)
class AssertNoOrphanEffects(EffectBase[None]):
    """check (c): every EffectBase subclass in the named packages is introduced by some domain in the table. A violation is OrphanEffectError.

    packages are import names of modules or packages (a package is walked down). known_orphans is a declared orphan (if it is no longer an orphan,
    a stale DomainCheckError). A module that cannot be imported is also a DomainCheckError (the original exception is __cause__).
    """

    packages: tuple[str, ...]
    known_orphans: tuple[type, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.packages, list | tuple):
            raise TypeError(
                "AssertNoOrphanEffects.packages takes a list of import names, "
                f"got {type(self.packages).__name__}"
            )
        object.__setattr__(self, "packages", tuple(self.packages))
        object.__setattr__(
            self, "known_orphans", _types(self.known_orphans, "AssertNoOrphanEffects.known_orphans")
        )


#: All effects the handler answers (covered exhaustively by the match in _answer).
DomainEffect = (
    RegisterDomain
    | GetDomain
    | HandledEffects
    | AssertDomainCovered
    | AssertRegisteredDomainsCovered
    | AssertNoOrphanEffects
)


def _table(domains: list[Domain] | tuple[Domain, ...]) -> DomainTable:
    """Build a separate table holding the given domains (duplicate names, a second introduction, and non-Domain values are rejected at construction)."""
    if not isinstance(domains, list | tuple):
        raise TypeError(
            f"domain_registry(domains=…) takes a list of Domain, got {type(domains).__name__}"
        )
    table = DomainTable()
    for domain in domains:
        table.register(domain)
    return table


def _answer(table: DomainTable, effect: DomainEffect) -> object:
    """Answer one effect from the table (a check violation is raised as the exception itself and thrown back to where the handler raised it)."""
    match effect:
        case RegisterDomain(domain=domain):
            return table.register(domain)
        case GetDomain(name=name):
            return table.get(name)
        case HandledEffects(handler=entry_handler, vocabulary=vocabulary):
            return handled_effects(entry_handler, vocabulary=vocabulary)
        case AssertDomainCovered(domain=domain, known_uncovered=known):
            return assert_domain_covered(table, domain, known_uncovered=known)
        case AssertRegisteredDomainsCovered(known_uncovered=known):
            return assert_registered_domains_covered(table, known_uncovered=known)
        case AssertNoOrphanEffects(packages=packages, known_orphans=known):
            return assert_no_orphan_effects(table, packages, known_orphans=known)
        case _:
            # the handler annotation skips everything outside the 6 above, so this is unreachable -- if it happens, refuse instead of silently returning None.
            raise TypeError(f"domain_registry does not answer {type(effect).__name__}")


def domain_registry(*, domains: list[Domain] | tuple[Domain, ...] | None = None) -> ProgramHandler:
    """The handler that answers the domain-table effects (RegisterDomain, GetDomain, HandledEffects, and the 3 checks).

    Without domains, it reads the process table (where defdomain registers) when answering; with domains, it answers from a separate table holding only those
    domains (domains=[] is an empty table, not the process table -- the replacement for the former isolated_registry).

    The separate table is created once when the factory is called: as long as the same handler value is reused, domains registered with RegisterDomain remain, so if each test
    needs a fresh table, call the factory again (a pytest fixture is function-scoped). Overlapping names in domains, a second introduction, and values that are
    not Domain raise at factory call time rather than inside a program (a child of DomainRegistrationError, or TypeError).

    Usage: domain_registry()(program), domain_registry(domains=[domain])(program).
    """
    table = PROCESS_TABLE if domains is None else _table(domains)

    @handler
    def answer_domain_registry(
        effect: RegisterDomain
        | GetDomain
        | HandledEffects
        | AssertDomainCovered
        | AssertRegisteredDomainsCovered
        | AssertNoOrphanEffects,
        k: K,
    ):
        try:
            value = _answer(table, effect)
        except (DomainError, KeyError) as failure:
            return (yield TransferThrow(k, failure))
        return (yield Transfer(k, value))

    return answer_domain_registry
