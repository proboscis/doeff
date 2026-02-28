"""Effect definitions for pinjected bridge operations."""


from dataclasses import dataclass
from typing import Any

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class PinjectedEffectBase(EffectBase):
    """Base class for pinjected bridge domain effects."""


@dataclass(frozen=True, kw_only=True)
class PinjectedResolve(PinjectedEffectBase):
    """Resolve a dependency key from a pinjected resolver."""

    key: Any


@dataclass(frozen=True, kw_only=True)
class PinjectedProvide(PinjectedEffectBase):
    """Override a dependency binding for the current program run."""

    key: Any
    value: Any


__all__ = [
    "PinjectedEffectBase",
    "PinjectedProvide",
    "PinjectedResolve",
]
