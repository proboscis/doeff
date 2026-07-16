from dataclasses import FrozenInstanceError

import pytest
from doeff_vm import EffectBase

from doeff_domain.registry import (
    Domain,
    DomainLaw,
    DomainTerm,
    domain_for_effect,
    get_domain,
    register_domain,
    registered_domains,
)


class FirstEffect(EffectBase):
    pass


class SecondEffect(EffectBase):
    pass


def test_domain_is_frozen_pure_data_and_registers_by_name():
    term = DomainTerm(
        name="is-terminal",
        home="sample.predicates",
        description="Canonical terminal predicate",
    )
    law = DomainLaw(
        name="single-home",
        statement="An effect has one introducing domain",
        counterexamples=["two declarations introduce the same class"],
    )
    domain = register_domain(
        Domain(
            name="sample",
            title="Sample vocabulary",
            effects=[FirstEffect],
            terms=[term],
            laws=[law],
            adrs=["ADR-SAMPLE-001"],
            docs=["docs/sample.md"],
        )
    )

    assert get_domain("sample") is domain
    assert registered_domains() == (domain,)
    assert domain_for_effect(FirstEffect) is domain
    assert domain.effects == (FirstEffect,)
    assert domain.terms == (term,)
    assert law.counterexamples == ("two declarations introduce the same class",)
    with pytest.raises(FrozenInstanceError):
        domain.title = "changed"


def test_duplicate_domain_name_fails_immediately():
    register_domain(Domain(name="shared", title="First"))

    with pytest.raises(ValueError, match="duplicate domain name.*shared"):
        register_domain(Domain(name="shared", title="Second"))


def test_effect_can_be_introduced_by_exactly_one_domain_and_error_names_both():
    first = register_domain(Domain(name="first-home", title="First", effects=[FirstEffect]))

    with pytest.raises(ValueError) as error:
        register_domain(Domain(name="second-home", title="Second", effects=[FirstEffect]))

    message = str(error.value)
    assert "first-home" in message
    assert "second-home" in message
    assert "FirstEffect" in message
    assert registered_domains() == (first,)


def test_includes_can_reference_an_effect_domain_without_reintroducing_its_effects():
    introducing = register_domain(
        Domain(name="introducing", title="Introducing", effects=[SecondEffect])
    )
    including = register_domain(
        Domain(name="including", title="Including", includes=[introducing])
    )

    assert including.includes == (introducing,)
    assert domain_for_effect(SecondEffect) is introducing
