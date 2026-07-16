from doeff_domain.checks import assert_domain_covered, assert_no_orphan_effects
from doeff_domain.registry import domain_for_effect


def test_core_effect_domains_cover_handlers_and_leave_no_orphan_effects():
    from doeff_domain import dogfood

    assert dogfood.CORE_EFFECT_DOMAINS
    for domain in dogfood.CORE_EFFECT_DOMAINS:
        assert_domain_covered(domain)
        for effect_class in domain.effects:
            assert domain_for_effect(effect_class) is domain

    assert_no_orphan_effects(packages=["doeff_core_effects"])
