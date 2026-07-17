"""Fixture effect classes for doeff-domain tests."""

from doeff_vm import EffectBase


class FixtureAlpha(EffectBase):
    """Test effect: alpha."""


class FixtureBeta(EffectBase):
    """Test effect: beta."""


class FixtureGamma(EffectBase):
    """Test effect: gamma (introduced by domain_defdomain_fixture at import time)."""


class FixtureDelta(EffectBase):
    """Test effect: delta."""


def make_effect_class(name: str) -> type:
    """Create a distinct EffectBase subclass with the given __name__.

    __module__ is stamped with a synthetic marker so orphan scans over this
    module never pick these classes up.
    """
    return type(name, (EffectBase,), {"__module__": __name__ + ".synthetic"})


def plain_installer(body):
    """A raw Program -> Program callable with no domain metadata."""
    return body
