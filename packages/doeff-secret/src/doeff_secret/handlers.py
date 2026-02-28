"""Built-in handlers for provider-independent secret workflows."""


import os
from collections.abc import Callable, Mapping
from typing import Any

from doeff import Delegate, Effect, Resume, do

from .effects import GetSecret

ProtocolHandler = Callable[[Any, Any], Any]


def _normalize_secret_id(secret_id: str) -> str:
    normalized_chars = [ch if ch.isalnum() else "_" for ch in secret_id.upper()]
    normalized = "".join(normalized_chars)
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_")


def _normalize_prefix(prefix: str) -> str:
    if not prefix:
        return ""
    normalized = _normalize_secret_id(prefix)
    if not normalized:
        return ""
    return f"{normalized}_"


def _resolve_env_secret(
    secret_id: str,
    *,
    environ: Mapping[str, str],
    prefix: str,
    include_raw_secret_id: bool,
) -> str | None:
    normalized_secret_id = _normalize_secret_id(secret_id)
    candidates = [f"{prefix}{normalized_secret_id}"]
    if include_raw_secret_id:
        candidates.append(secret_id)

    for key in candidates:
        value = environ.get(key)
        if value is not None:
            return value
    return None


def env_var_handler(
    *,
    environ: Mapping[str, str] | None = None,
    prefix: str = "",
    include_raw_secret_id: bool = True,
) -> ProtocolHandler:
    """Build a fallback handler that resolves GetSecret from environment variables.

    This handler delegates when the secret is not present.
    """

    active_environ = os.environ if environ is None else environ
    normalized_prefix = _normalize_prefix(prefix)

    @do
    def handle_get_secret(effect: Effect, k: Any):
        if not isinstance(effect, GetSecret):
            yield Delegate()
            return None
        value = _resolve_env_secret(
            effect.secret_id,
            environ=active_environ,
            prefix=normalized_prefix,
            include_raw_secret_id=include_raw_secret_id,
        )
        if value is None:
            yield Delegate()
            return None
        return (yield Resume(k, value))

    @do
    def handler(effect: Effect, k: Any):
        if isinstance(effect, GetSecret):
            return (yield handle_get_secret(effect, k))
        yield Delegate()

    return handler


def env_var_handlers(
    *,
    environ: Mapping[str, str] | None = None,
    prefix: str = "",
    include_raw_secret_id: bool = True,
) -> ProtocolHandler:
    """Build strict env-var handlers that raise when the secret is missing.

    This variant raises ``KeyError`` when a requested secret is missing.
    """

    active_environ = os.environ if environ is None else environ
    normalized_prefix = _normalize_prefix(prefix)

    @do
    def handler(effect: Effect, k: Any):
        if not isinstance(effect, GetSecret):
            yield Delegate()
            return None
        value = _resolve_env_secret(
            effect.secret_id,
            environ=active_environ,
            prefix=normalized_prefix,
            include_raw_secret_id=include_raw_secret_id,
        )
        if value is None:
            raise KeyError(f"Secret not found in environment variables: {effect.secret_id}")
        return (yield Resume(k, value))

    return handler


__all__ = [
    "ProtocolHandler",
    "env_var_handler",
    "env_var_handlers",
]
