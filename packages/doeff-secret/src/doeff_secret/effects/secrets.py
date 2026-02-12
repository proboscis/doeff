"""Provider-agnostic effect definitions for secret operations."""

from __future__ import annotations

from dataclasses import dataclass

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class SecretEffectBase(EffectBase):
    """Base class for secret-domain effects."""


@dataclass(frozen=True, kw_only=True)
class GetSecret(SecretEffectBase):
    """Retrieve a secret value from a provider."""

    secret_id: str
    version: str = "latest"


@dataclass(frozen=True, kw_only=True)
class SetSecret(SecretEffectBase):
    """Create or update a secret value."""

    secret_id: str
    value: str | bytes


@dataclass(frozen=True, kw_only=True)
class ListSecrets(SecretEffectBase):
    """List available secrets."""

    filter: str | None = None


@dataclass(frozen=True, kw_only=True)
class DeleteSecret(SecretEffectBase):
    """Delete a secret."""

    secret_id: str


__all__ = [
    "DeleteSecret",
    "GetSecret",
    "ListSecrets",
    "SecretEffectBase",
    "SetSecret",
]
