"""Polling utilities built on doeff-time effects."""

import doeff_hy as _doeff_hy  # noqa: F401  # registers Hy import hooks

from ._polling_impl import poll_until

__all__ = ["poll_until"]
