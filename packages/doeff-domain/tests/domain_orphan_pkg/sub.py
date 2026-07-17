"""Submodule of the orphan-scan fixture package."""

from doeff_vm import EffectBase


class PkgSubEffect(EffectBase):
    """Effect defined in a submodule — package walk must find it."""
