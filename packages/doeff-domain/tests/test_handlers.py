import importlib

import pytest
from doeff_domain.handlers import handled_effects, handles
from doeff_domain.registry import Domain


def test_handles_annotation_takes_precedence_over_structural_derivation():
    fixture = importlib.import_module("doeff_domain_test_handlers")

    @handles(fixture.AlphaEffect)
    def annotated_handler(program):
        return program

    annotated_handler.__dict__["__doeff_body__"] = fixture.direct_handler.__doeff_body__

    assert handled_effects(annotated_handler, Domain(name="target", title="Target")) == {
        fixture.AlphaEffect
    }


def test_real_defhandler_products_derive_no_arg_factory_lazy_and_guard_clauses():
    fixture = importlib.import_module("doeff_domain_test_handlers")
    domain = Domain(
        name="fixture",
        title="Fixture",
        effects=[fixture.AlphaEffect, fixture.BetaEffect, fixture.GammaEffect],
    )

    assert handled_effects(fixture.direct_handler, domain) == {
        fixture.AlphaEffect,
        fixture.BetaEffect,
    }
    assert handled_effects(fixture.configured_handler, domain) == {fixture.GammaEffect}


def test_defhandler_name_resolution_falls_back_to_domain_effect_class_name():
    fixture = importlib.import_module("doeff_domain_test_handlers")
    fallback_effect = fixture.FallbackEffect
    fixture.__dict__.pop("FallbackEffect")
    domain = Domain(name="fallback", title="Fallback", effects=[fallback_effect])

    assert handled_effects(fixture.fallback_handler, domain) == {fallback_effect}


def test_unresolved_defhandler_clause_fails_loudly():
    fixture = importlib.import_module("doeff_domain_test_handlers")
    fixture.direct_handler.__doeff_body__ = [
        *fixture.direct_handler.__doeff_body__,
        ["MissingEffect"],
    ]
    domain = Domain(name="fixture", title="Fixture", effects=[fixture.AlphaEffect])

    with pytest.raises(AssertionError, match=r"MissingEffect.*direct-handler"):
        handled_effects(fixture.direct_handler, domain)


def test_handler_without_annotation_or_defhandler_structure_fails_loudly():
    def opaque_handler(program):
        return program

    with pytest.raises(AssertionError, match=r"opaque_handler.*declaration.*derive"):
        handled_effects(opaque_handler, Domain(name="target", title="Target"))
