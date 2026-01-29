"""Google Secret Manager integration for doeff."""

from .client import SecretManagerClient, get_secret_manager_client
from .secrets import access_secret

__all__ = [
    "SecretManagerClient",
    "access_secret",
    "get_secret_manager_client",
]
