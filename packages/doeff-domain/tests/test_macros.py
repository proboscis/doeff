import importlib

from doeff_domain.registry import Domain, domain_for_effect, get_domain


def test_defdomain_macro_builds_and_registers_domain_with_real_class_references():
    fixture = importlib.import_module("doeff_domain_macro_fixture")

    assert isinstance(fixture.sample_domain, Domain)
    assert get_domain("sample-domain") is fixture.sample_domain
    assert fixture.sample_domain.effects == (fixture.MacroEffect,)
    assert domain_for_effect(fixture.MacroEffect) is fixture.sample_domain
    assert fixture.sample_domain.terms[0].home == "sample.predicates"
    assert fixture.sample_domain.laws[0].name == "single-home"
