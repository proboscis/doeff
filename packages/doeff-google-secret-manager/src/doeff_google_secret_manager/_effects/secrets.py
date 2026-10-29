"""Backward-compatible re-export for secret effects.

This module is deprecated. Import effects from `doeff_secret._effects` instead.
"""


import warnings

from doeff_secret import DeleteSecret, GetSecret, ListSecrets, SecretEffectBase, SetSecret

warnings.warn(
    "Importing from doeff_google_secret_manager._effects.secrets is deprecated; "
    "use doeff_secret._effects instead.",
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
