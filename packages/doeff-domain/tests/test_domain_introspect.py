"""Handles declarations and two-layer derivation of the handled set (ADR-DOE-DOMAIN-001 D5/D6).

Derivation is read with effect HandledEffects, answered by domain_registry (ADR-DOE-API-001 R29). Raw Python
handlers are placed in a domain's handlers with the declaration type Handles(handler, effect ...) (the former function handles() tagged the handler after the fact).

The derivation tests run against real defhandler products (no-arg form, arg form, lazy clauses, :when guards).
doeff-hy is imported only from the test environment -- doeff-domain itself
is independent of doeff-hy through attribute duck typing (D1/D6).
"""

import doeff_hy  # noqa: F401 — import hook registration for the .hy fixture module (test-only)
import domain_defhandler_fixtures as fixtures
import pytest
from doeff_domain import (
    DomainCheckError,
    DomainDefinitionError,
    HandledEffects,
    Handles,
    domain_registry,
)
from domain_test_effects import (
    FixtureAlpha,
    FixtureBeta,
    FixtureDelta,
    FixtureGamma,
    plain_installer,
)

from doeff import run


def handled_effects(handler, *, vocabulary=()) -> frozenset[type]:
    """Run effect HandledEffects under domain_registry and read the handler's handled set."""
    return run(domain_registry(domains=[])(HandledEffects(handler, vocabulary=list(vocabulary))))


class TestHandlesDeclaration:
    def test_holds_the_handler_and_the_effects(self):
        def raw_handler(body):
            return body

        declared = Handles(raw_handler, FixtureAlpha, FixtureBeta)
        assert declared.handler is raw_handler
        assert declared.effects == (FixtureAlpha, FixtureBeta)
        assert handled_effects(declared) == frozenset({FixtureAlpha, FixtureBeta})

    def test_declares_a_foreign_function_without_touching_it(self):
        # D5: a raw handler already defined in another module can be declared without attaching anything to it
        declared = Handles(plain_installer, FixtureAlpha)
        assert handled_effects(declared) == frozenset({FixtureAlpha})
        assert not hasattr(plain_installer, "__doeff_handles__")

    def test_handles_requires_at_least_one_effect(self):
        with pytest.raises(DomainDefinitionError, match="Handles"):
            Handles(plain_installer)

    def test_handles_rejects_non_effect_classes(self):
        with pytest.raises(DomainDefinitionError, match="EffectBase"):
            Handles(plain_installer, "FixtureAlpha")  # type: ignore[arg-type]

    def test_handles_is_a_frozen_value(self):
        declared = Handles(plain_installer, FixtureAlpha)
        assert declared == Handles(plain_installer, FixtureAlpha)
        with pytest.raises(AttributeError):
            declared.effects = (FixtureBeta,)  # type: ignore[misc]


class TestDerivationLayers:
    def test_the_declaration_wins_over_defhandler_body(self):
        handler = fixtures.fixture_plain_handler
        assert handled_effects(Handles(handler, FixtureDelta)) == frozenset({FixtureDelta})
        assert handled_effects(handler) == frozenset({FixtureAlpha, FixtureBeta})

    def test_the_handlers_own_mark_is_read(self):
        # the __doeff_handles__ mark set by core's installer is read as the handled set of a handler with no declaration
        def marked(body):
            return body

        marked.__doeff_handles__ = (FixtureGamma,)
        assert handled_effects(marked) == frozenset({FixtureGamma})

    def test_neither_declaration_nor_attribute_fails_loud(self):
        def anonymous(body):
            return body

        with pytest.raises(DomainCheckError, match="__doeff_handles__"):
            handled_effects(anonymous)


class TestDefhandlerDerivation:
    def test_no_arg_form(self):
        derived = handled_effects(fixtures.fixture_plain_handler)
        assert derived == frozenset({FixtureAlpha, FixtureBeta})

    def test_factory_form(self):
        derived = handled_effects(fixtures.fixture_factory_handler)
        assert derived == frozenset({FixtureAlpha, FixtureBeta})

    def test_lazy_clause_skipped(self):
        derived = handled_effects(fixtures.fixture_lazy_handler)
        assert derived == frozenset({FixtureGamma})

    def test_lazy_val_clause_skipped(self):
        # lazy clause heads come in 3 kinds (lazy / lazy-val / lazy-var) -- none are handling declarations
        derived = handled_effects(fixtures.fixture_lazy_val_handler)
        assert derived == frozenset({FixtureAlpha})

    def test_lazy_var_clause_skipped(self):
        derived = handled_effects(fixtures.fixture_lazy_var_handler)
        assert derived == frozenset({FixtureBeta})

    def test_session_clause_skipped(self):
        # session val / session var (the successor spelling of lazy) are not handling declarations either
        derived = handled_effects(fixtures.fixture_session_handler)
        assert derived == frozenset({FixtureGamma})

    def test_when_guard_counts_as_participation(self):
        derived = handled_effects(fixtures.fixture_guarded_handler)
        assert derived == frozenset({FixtureAlpha, FixtureDelta})

    def test_vocabulary_fallback_when_module_attr_missing(self, monkeypatch):
        # D6: a clause that cannot be resolved via sys.modules attributes falls back to
        # a string match against the effect class names of the domain being checked
        monkeypatch.delattr(fixtures, "FixtureAlpha")
        derived = handled_effects(
            fixtures.fixture_plain_handler, vocabulary=[FixtureAlpha, FixtureBeta]
        )
        assert derived == frozenset({FixtureAlpha, FixtureBeta})

    def test_unresolvable_clause_fails_loud(self, monkeypatch):
        # D6: if both resolutions fail, it is a check error (never dropped silently)
        monkeypatch.delattr(fixtures, "FixtureAlpha")
        with pytest.raises(DomainCheckError) as excinfo:
            handled_effects(fixtures.fixture_plain_handler, vocabulary=[FixtureBeta])
        message = str(excinfo.value)
        assert "FixtureAlpha" in message
        assert "fixture-plain-handler" in message
