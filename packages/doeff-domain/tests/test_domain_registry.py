"""Domain データ構造と registry の意味論 (ADR-DOE-DOMAIN-001 D2/D3)。"""

import pytest
from domain_test_effects import FixtureAlpha, FixtureBeta, make_effect_class

from doeff_domain import (
    Domain,
    DomainDefinitionError,
    DomainLaw,
    DomainTerm,
    DuplicateDomainNameError,
    DuplicateEffectIntroductionError,
    get_domain,
    introducing_domain,
    isolated_registry,
    register_domain,
    registered_domains,
)


def make_domain(name="d-fixture", **kwargs):
    defaults = {"title": "fixture domain"}
    defaults.update(kwargs)
    return Domain(name=name, **defaults)


class TestDomainData:
    def test_domain_is_frozen(self):
        domain = make_domain()
        with pytest.raises(AttributeError):
            domain.title = "mutated"  # type: ignore[misc]

    def test_sequences_coerced_to_tuples(self):
        domain = make_domain(effects=[FixtureAlpha], adrs=["ADR-X"])
        assert domain.effects == (FixtureAlpha,)
        assert isinstance(domain.effects, tuple)
        assert domain.adrs == ("ADR-X",)

    def test_effects_must_be_classes_not_strings(self):
        # D2: 実クラス参照のみ — 文字列は import が壊れても fail しないので禁止
        with pytest.raises(DomainDefinitionError, match="FixtureAlpha"):
            make_domain(effects=["FixtureAlpha"])

    def test_effects_must_be_effectbase_subclasses(self):
        with pytest.raises(DomainDefinitionError, match="EffectBase"):
            make_domain(effects=[int])

    def test_duplicate_effect_within_domain_rejected(self):
        with pytest.raises(DomainDefinitionError, match="FixtureAlpha"):
            make_domain(effects=[FixtureAlpha, FixtureAlpha])

    def test_includes_must_be_domain_instances(self):
        with pytest.raises(DomainDefinitionError, match="includes"):
            make_domain(includes=["other-domain"])

    def test_terms_and_laws_types_validated(self):
        with pytest.raises(DomainDefinitionError, match="terms"):
            make_domain(terms=["is-terminal"])
        with pytest.raises(DomainDefinitionError, match="laws"):
            make_domain(laws=["single-home"])

    def test_term_and_law_payloads(self):
        term = DomainTerm(
            name="is-terminal", home="pkg.predicates", description="canonical predicate"
        )
        law = DomainLaw(
            name="single-home",
            statement="for_all v: canonical_declaration_count(v) == 1",
            counterexamples=("ACP core#8",),
        )
        domain = make_domain(terms=[term], laws=[law])
        assert domain.terms[0].home == "pkg.predicates"
        assert domain.laws[0].counterexamples == ("ACP core#8",)


class TestRegistration:
    def test_register_and_lookup(self):
        with isolated_registry():
            domain = register_domain(make_domain(name="d-reg", effects=[FixtureAlpha]))
            assert get_domain("d-reg") is domain
            assert introducing_domain(FixtureAlpha) is domain
            assert domain in registered_domains()

    def test_same_name_reregistration_raises(self):
        # D2: 同名 domain の再登録は即例外
        with isolated_registry():
            register_domain(make_domain(name="d-dup"))
            with pytest.raises(DuplicateDomainNameError, match="d-dup"):
                register_domain(make_domain(name="d-dup"))

    def test_second_introduction_raises_with_both_domain_names(self):
        # D3: 導入 1 / 包含 ∞ — 2 つ目の導入は両 domain 名入りの例外
        with isolated_registry():
            register_domain(make_domain(name="d-first", effects=[FixtureAlpha]))
            with pytest.raises(DuplicateEffectIntroductionError) as excinfo:
                register_domain(make_domain(name="d-second", effects=[FixtureAlpha]))
            message = str(excinfo.value)
            assert "d-first" in message
            assert "d-second" in message
            assert "FixtureAlpha" in message

    def test_includes_do_not_introduce(self):
        # 包含はいくつでも可 — 導入にはならない
        with isolated_registry():
            home = register_domain(make_domain(name="d-home", effects=[FixtureAlpha]))
            register_domain(make_domain(name="d-inc1", includes=[home]))
            register_domain(make_domain(name="d-inc2", includes=[home]))
            assert introducing_domain(FixtureAlpha).name == "d-home"

    def test_introduction_keyed_by_class_identity_not_name(self):
        # D3: キーはクラス同一性 — 同名の別クラスは両方導入できる
        with isolated_registry():
            first = make_effect_class("SameNameEffect")
            second = make_effect_class("SameNameEffect")
            register_domain(make_domain(name="d-id1", effects=[first]))
            register_domain(make_domain(name="d-id2", effects=[second]))
            assert introducing_domain(first).name == "d-id1"
            assert introducing_domain(second).name == "d-id2"

    def test_failed_registration_is_atomic(self):
        with isolated_registry():
            register_domain(make_domain(name="d-atomic", effects=[FixtureAlpha]))
            with pytest.raises(DuplicateEffectIntroductionError):
                register_domain(
                    make_domain(name="d-atomic2", effects=[FixtureBeta, FixtureAlpha])
                )
            with pytest.raises(KeyError):
                get_domain("d-atomic2")
            with pytest.raises(KeyError):
                introducing_domain(FixtureBeta)

    def test_get_domain_unknown_name_fails_loud(self):
        with isolated_registry():
            with pytest.raises(KeyError, match="no-such-domain"):
                get_domain("no-such-domain")

    def test_isolated_registry_starts_empty_and_restores(self):
        with isolated_registry():
            assert registered_domains() == ()
            register_domain(make_domain(name="d-temp", effects=[FixtureBeta]))
            assert get_domain("d-temp").name == "d-temp"
        with isolated_registry():
            with pytest.raises(KeyError):
                get_domain("d-temp")
            with pytest.raises(KeyError):
                introducing_domain(FixtureBeta)
