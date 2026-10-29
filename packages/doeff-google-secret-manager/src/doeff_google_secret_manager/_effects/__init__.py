"""Backward-compatible re-export for secret effects.

This module is deprecated. Import effects from `doeff_secret` instead.
"""


import warnings

from doeff_secret import DeleteSecret, GetSecret, ListSecrets, SecretEffectBase, SetSecret

warnings.warn(
    "The effects submodule of doeff_google_secret_manager is deprecated; "
    "import the effects from doeff_secret instead.",
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
