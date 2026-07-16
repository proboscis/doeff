import importlib

import pytest
from doeff_domain.checks import assert_domain_covered, assert_no_orphan_effects
from doeff_domain.handlers import handles
from doeff_domain.registry import Domain, register_domain
from doeff_vm import EffectBase


class CoveredEffect(EffectBase):
    pass


class MissingEffect(EffectBase):
    pass


def test_domain_coverage_reports_missing_introduced_effect_and_then_passes():
    @handles(CoveredEffect)
    def partial_handler(program):
        return program

    incomplete = Domain(
        name="coverage",
        title="Coverage",
        effects=[CoveredEffect, MissingEffect],
        handlers=[partial_handler],
    )

    with pytest.raises(AssertionError, match=r"coverage.*MissingEffect"):
        assert_domain_covered(incomplete)

    handles(CoveredEffect, MissingEffect)(partial_handler)
    assert_domain_covered(incomplete)


def test_coverage_excludes_effects_from_included_domains():
    included = Domain(name="included", title="Included", effects=[MissingEffect])
    including = Domain(name="including", title="Including", includes=[included])

    assert_domain_covered(including)


def test_orphan_check_reports_class_and_module_then_passes_when_registered():
    effects = importlib.import_module("orphan_effect_package.effects")
    register_domain(Domain(name="owned", title="Owned", effects=[effects.OwnedEffect]))

    with pytest.raises(AssertionError) as error:
        assert_no_orphan_effects(packages=["orphan_effect_package"])

    message = str(error.value)
    assert "OrphanEffect" in message
    assert "orphan_effect_package.effects" in message

    register_domain(
        Domain(name="formerly-orphan", title="Formerly orphan", effects=[effects.OrphanEffect])
    )
    assert_no_orphan_effects(packages=["orphan_effect_package"])
