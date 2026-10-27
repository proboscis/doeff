"""Fixture package for orphan scanning: the walk must reach submodules."""

from doeff import EffectBase


class PkgRootEffect(EffectBase):
    """Effect defined at the package root."""
