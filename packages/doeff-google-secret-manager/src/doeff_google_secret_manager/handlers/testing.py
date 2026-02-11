"""Backward-compatible in-memory handlers for Google Secret Manager effects."""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from typing import Any

from doeff_secret.testing import (
    InMemorySecretStore,
    ProtocolHandler,
    SeedValue,
    in_memory_handlers,
)


def mock_handlers(
    *,
    seed_data: Mapping[str, SeedValue] | None = None,
    project: str = "mock-project",
    store: InMemorySecretStore | None = None,
) -> dict[type[Any], ProtocolHandler]:
    """Build in-memory handler map for secret effects.

    Deprecated: use `doeff_secret.testing.in_memory_handlers(...)`.
    """

    warnings.warn(
        "doeff_google_secret_manager.handlers.mock_handlers is deprecated; "
        "use doeff_secret.testing.in_memory_handlers instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return in_memory_handlers(seed_data=seed_data, project=project, store=store)


__all__ = [
    "InMemorySecretStore",
    "ProtocolHandler",
    "SeedValue",
    "mock_handlers",
]
