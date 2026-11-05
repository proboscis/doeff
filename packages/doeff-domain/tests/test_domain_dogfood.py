"""D8 dogfood: declare the doeff-core-effects vocabulary as domains and wire up checks (a)(c).

MemoDeleteEffect / CacheDeleteEffect were once defined and exported as vocabulary, but
no handler processed them (real drift), so they were declared as known_uncovered
(investigated 2026-07-17). Handlers for both effects were
implemented later, and the declarations were removed as the stale-ratchet enforces -- check (a) is
green with no declarations. The ratchet firing itself is pinned by test_stale_known_uncovered_declaration_rejected.

The registry is read via effects, and domain_registry() answers from the process registry (where the dogfood module registers on import)
(ADR-DOE-API-001 R29).
"""

import doeff_domain._core_effects_domains as dogfood  # noqa: F401 — registers on import
import pytest
from doeff_core_effects import (
    Ask,
    CacheDeleteEffect,
    Get,
    HttpRequest,
    Local,
    MemoDeleteEffect,
    MemoExistsEffect,
    MemoGetEffect,
    MemoPutEffect,
    Put,
)
from doeff_domain import (
    AssertDomainCovered,
    AssertNoOrphanEffects,
    AssertRegisteredDomainsCovered,
    DomainCheckError,
    GetDomain,
    HandledEffects,
    Handles,
    domain_registry,
)
from doeff_domain._registry import introducing_domain

from doeff import run


def ask(effect):
    """Run one effect under the handler backed by the process registry."""
    return run(domain_registry()(effect))


def get_domain(name):
    """Look up a domain by name in the process registry (effect GetDomain)."""
    return ask(GetDomain(name))


def handled_effects(handler, *, vocabulary=()):
    """Read a handler's handled-effect set (effect HandledEffects)."""
    return ask(HandledEffects(handler, vocabulary=list(vocabulary)))


DOGFOOD_DOMAIN_NAMES = [
    "doeff-reader",
    "doeff-state",
    "doeff-writer",
    "doeff-slog",
    "doeff-error",
    "doeff-scope",
    "doeff-listen",
    "doeff-await",
    "doeff-scheduler",
    "doeff-http",
    "doeff-memo",
    "doeff-cache",
    "doeff-outcome",
    "doeff-file",
    "doeff-http-server",
    "doeff-ws-client",
    "doeff-sql",
    "doeff-process",
    "doeff-warm-process",
    "doeff-process-exit",
    "doeff-channel",
    "doeff-compute",
    "doeff-stop-signal",
    "doeff-vm-probe",
]


def test_all_dogfood_domains_registered():
    for name in DOGFOOD_DOMAIN_NAMES:
        assert get_domain(name).title


@pytest.mark.parametrize("name", DOGFOOD_DOMAIN_NAMES)
def test_dogfood_coverage_green(name):
    # check (a): introduced effects ⊆ union of handlers' handled sets — green with no declarations
    ask(AssertDomainCovered(get_domain(name)))


def test_every_dogfood_domain_is_covered_in_a_table_of_their_own():
    # check (a) over every domain in the registry — the process registry also holds declarations from other tests, so run on a separate registry of only the dogfood domains
    dogfood_domains = [get_domain(name) for name in DOGFOOD_DOMAIN_NAMES]
    run(domain_registry(domains=dogfood_domains)(AssertRegisteredDomainsCovered()))


def test_stale_known_uncovered_declaration_rejected():
    # proof that the ratchet fires: the delete effects are now covered, so keeping the old known_uncovered
    # declaration fails as stale (the mechanism forcing removal of declarations works)
    for name, effect in (("doeff-memo", MemoDeleteEffect), ("doeff-cache", CacheDeleteEffect)):
        with pytest.raises(DomainCheckError, match="stale known_uncovered"):
            ask(AssertDomainCovered(name, known_uncovered=[effect]))


def test_no_orphan_effects_in_core_effects():
    # check (c): import-scan all of doeff_core_effects — every EffectBase descendant is
    # introduced by some domain
    ask(AssertNoOrphanEffects(["doeff_core_effects"]))


def test_http_and_memo_domains_use_defhandler_derivation():
    # real dogfood of two-layer derivation: defhandler-made factories derive from __doeff_body__ without annotations
    http = get_domain("doeff-http")
    assert len(http.handlers) == 3
    for handler in http.handlers:
        assert not hasattr(handler, "__doeff_handles__")
        derived = handled_effects(handler, vocabulary=http.effects)
        assert derived == frozenset({HttpRequest})

    memo = get_domain("doeff-memo")
    (memo_handler,) = memo.handlers
    assert not hasattr(memo_handler, "__doeff_handles__")
    derived = handled_effects(memo_handler, vocabulary=memo.effects)
    # adding the MemoDeleteEffect clause is automatically reflected in structural derivation (D6)
    assert derived == frozenset({MemoExistsEffect, MemoGetEffect, MemoPutEffect, MemoDeleteEffect})


def test_scope_domain_includes_reader_vocabulary():
    scope = get_domain("doeff-scope")
    assert get_domain("doeff-reader") in scope.includes
    # includes is not introduction — Local is introduced by doeff-scope
    assert introducing_domain(Local).name == "doeff-scope"


def test_raw_handlers_are_declared_in_the_domain_without_touching_them():
    # D5: do not edit core-effects; declare via Handles in the dogfood domain's handlers — mark nothing on the handler
    from doeff_core_effects import lazy_ask, reader, state

    declared = {
        entry.handler: entry
        for name in ("doeff-reader", "doeff-scope", "doeff-state")
        for entry in get_domain(name).handlers
        if isinstance(entry, Handles)
    }
    assert handled_effects(declared[reader]) == frozenset({Ask})
    assert handled_effects(declared[lazy_ask]) == frozenset({Ask, Local})
    assert handled_effects(declared[state]) == frozenset({Get, Put})
    assert not hasattr(reader, "__doeff_handles__")
    assert not hasattr(lazy_ask, "__doeff_handles__")
