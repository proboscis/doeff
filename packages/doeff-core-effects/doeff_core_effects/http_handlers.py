"""Compatibility exports for Hy HTTP handlers.

Without the extra ``http`` (Hy and httpx), importing this module fails with the
line that installs it (extra_hints.py).
"""

from doeff_core_effects.extra_hints import extra_needed

with extra_needed("http", "the HTTP client handler is written in Hy and sends with httpx"):
    import doeff_hy as _doeff_hy  # noqa: F401  # registers Hy import hooks

    from doeff_core_effects._http_handlers_impl import (
        CLIENT_KEEPALIVE_SECONDS,
        http_client_factory,
        http_fixture_handler,
        http_production_handler,
    )

__all__ = [
    "CLIENT_KEEPALIVE_SECONDS",
    "http_client_factory",
    "http_fixture_handler",
    "http_production_handler",
]
