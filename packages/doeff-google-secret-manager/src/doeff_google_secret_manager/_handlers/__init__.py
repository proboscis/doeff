"""Handlers for Google Secret Manager effects."""


from .production import production_handlers
from .testing import mock_handlers

# The in-memory store is doeff-secret's: read it from doeff_secret, not from here.
__all__ = [
    "mock_handlers",
    "production_handlers",
]
