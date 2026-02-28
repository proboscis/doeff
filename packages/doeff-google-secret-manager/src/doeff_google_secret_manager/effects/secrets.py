"""Backward-compatible re-export for secret effects.

This module is deprecated. Import effects from `doeff_secret.effects` instead.
"""


import warnings

from doeff_secret.effects import DeleteSecret, GetSecret, ListSecrets, SecretEffectBase, SetSecret

warnings.warn(
    "Importing from doeff_google_secret_manager.effects.secrets is deprecated; "
    "use doeff_secret.effects instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "DeleteSecret",
    "GetSecret",
    "ListSecrets",
    "SecretEffectBase",
    "SetSecret",
]
