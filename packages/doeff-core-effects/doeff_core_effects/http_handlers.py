"""Compatibility exports for Hy HTTP handlers."""

import doeff_hy as _doeff_hy  # noqa: F401  # registers Hy import hooks

from doeff_core_effects._http_handlers_impl import (
    http_fixture_handler,
    http_production_handler,
)

__all__ = ["http_fixture_handler", "http_production_handler"]
