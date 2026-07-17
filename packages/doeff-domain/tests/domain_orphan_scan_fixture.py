"""Fixture module for orphan-effect scanning tests (check (c)).

Defines two EffectBase subclasses and re-exports a third defined elsewhere:
the scan must count only classes DEFINED here, never re-exports.
"""

from doeff_vm import EffectBase
from domain_test_effects import FixtureAlpha as ReExportedFixtureAlpha  # noqa: F401


class OwnedFixtureEffect(EffectBase):
    """Effect that tests will introduce into a domain."""


class StrayFixtureEffect(EffectBase):
    """Effect that tests will leave un-introduced (the orphan)."""


NOT_AN_EFFECT = object()
