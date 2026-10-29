"""Backward-compatible in-memory handlers for Google Secret Manager effects."""


import warnings
from collections.abc import Mapping

from doeff_secret import InMemorySecretStore, SeedValue, in_memory_handlers
from doeff_secret._testing import ProtocolHandler


def mock_handlers(
    *,
    seed_data: Mapping[str, SeedValue] | None = None,
    project: str = "mock-project",
    store: InMemorySecretStore | None = None,
) -> ProtocolHandler:
    """Build an in-memory protocol handler for secret effects.

    Deprecated: use `doeff_secret.in_memory_handlers(...)`.
    """

    warnings.warn(
        "doeff_google_secret_manager.mock_handlers is deprecated; "
        "use doeff_secret.in_memory_handlers instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return in_memory_handlers(seed_data=seed_data, project=project, store=store)


# InMemorySecretStore, ProtocolHandler and SeedValue are doeff-secret's: read them from doeff_secret.
__all__ = [
    "mock_handlers",
]
