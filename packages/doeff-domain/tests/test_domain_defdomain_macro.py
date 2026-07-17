"""defdomain マクロ (ADR-DOE-DOMAIN-001 D4)。"""

import sys
import types

import doeff_hy  # noqa: F401 — .hy fixture module の import hook 登録(test-only)
import hy
import hy.macros
import pytest
from doeff_domain import (
    DuplicateDomainNameError,
    get_domain,
    introducing_domain,
    isolated_registry,
)
from domain_test_effects import FixtureGamma, make_effect_class


def _eval_defdomain(code: str, **extra_globals):
    """Evaluate Hy source with the defdomain macro available."""
    module = types.ModuleType("defdomain_eval_module")
    sys.modules[module.__name__] = module
    try:
        module.__dict__.update(extra_globals)
        hy.macros.require("doeff_domain.macros", module, assignments=[["defdomain", "defdomain"]])
        result = None
        for form in hy.read_many(code):
            result = hy.eval(form, module.__dict__, module=module)
        return module, result
    finally:
        del sys.modules[module.__name__]


def test_module_level_defdomain_registers_at_import_time():
    import domain_defdomain_fixture as fixture_module

    domain = get_domain("fixture-macro-domain")
    assert domain is fixture_module.fixture_macro_domain
    assert domain.effects == (FixtureGamma,)
    assert introducing_domain(FixtureGamma) is domain
    assert domain.terms[0].name == "fixture-term"
    assert domain.laws[0].name == "fixture-law"
    assert domain.adrs == ("ADR-DOE-DOMAIN-001",)
    assert domain.docs.startswith("macro fixture")


def test_defdomain_binds_name_and_defaults():
    effect = make_effect_class("MacroBindEffect")
    with isolated_registry():
        module, _ = _eval_defdomain(
            '(defdomain macro-bind-domain :title "bound" :effects [MacroBindEffect])',
            MacroBindEffect=effect,
        )
        domain = get_domain("macro-bind-domain")
        assert module.macro_bind_domain is domain
        assert domain.title == "bound"
        assert domain.effects == (effect,)
        assert domain.includes == ()
        assert domain.handlers == ()
        assert domain.terms == ()
        assert domain.laws == ()
        assert domain.adrs == ()
        assert domain.docs == ""


def test_defdomain_requires_title():
    with isolated_registry(), pytest.raises(Exception, match="defdomain requires :title"):
        _eval_defdomain("(defdomain macro-missing-title :effects [])")


def test_defdomain_rejects_unknown_keys():
    # :effect のような typo を黙って捨てない(fail loud)
    with isolated_registry(), pytest.raises(Exception, match="unknown key"):
        _eval_defdomain('(defdomain macro-typo-domain :title "t" :effect [])')


def test_defdomain_duplicate_name_raises_at_registration():
    with isolated_registry():
        _eval_defdomain('(defdomain macro-dup-domain :title "first")')
        with pytest.raises(DuplicateDomainNameError, match="macro-dup-domain"):
            _eval_defdomain('(defdomain macro-dup-domain :title "second")')
