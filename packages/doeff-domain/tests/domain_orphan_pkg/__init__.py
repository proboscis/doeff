"""Fixture package for orphan scanning: the walk must reach submodules."""

from doeff_vm import EffectBase


class PkgRootEffect(EffectBase):
    """Effect defined at the package root."""
