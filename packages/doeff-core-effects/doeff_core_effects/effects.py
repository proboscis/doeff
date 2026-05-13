"""
Core effects — Ask, Get, Put, Tell, HttpRequest.

These are EffectBase subclasses. Yield them from @do functions.
Handlers (reader, state, writer) handle them.
"""

from typing import Any

from doeff_vm import EffectBase

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


class Ask(EffectBase):
    """Reader effect: get a value from the environment by key."""
    def __init__(self, key):
        super().__init__()
        self.key = key

    def __repr__(self):
        return f"Ask({self.key!r})"


class Get(EffectBase):
    """State effect: get a value from mutable state by key."""
    def __init__(self, key):
        super().__init__()
        self.key = key

    def __repr__(self):
        return f"Get({self.key!r})"


class Put(EffectBase):
    """State effect: set a value in mutable state."""
    def __init__(self, key, value):
        super().__init__()
        self.key = key
        self.value = value

    def __repr__(self):
        return f"Put({self.key!r}, {self.value!r})"


def Tell(message):  # noqa: N802
    """Convenience: Tell(message) → WriterTellEffect(message)."""
    return WriterTellEffect(message)


class Local(EffectBase):
    """Scoped environment injection: run program with overridden env entries.

    yield Local({key: value, ...}, program) → result of program
    """
    def __init__(self, env, program):
        super().__init__()
        self.env = env
        self.program = program

    def __repr__(self):
        return f"Local({self.env!r}, ...)"


class Listen(EffectBase):
    """Collect all effects of given types emitted during program execution.

    yield Listen(program, types=(WriterTellEffect,)) → (result, collected)
    """
    def __init__(self, program, types=None):
        super().__init__()
        self.program = program
        self.types = types

    def __repr__(self):
        return "Listen(...)"


class Await(EffectBase):
    """Await a Python coroutine or future. Bridges async into doeff.

    yield Await(some_coroutine) → result
    """
    def __init__(self, coroutine):
        super().__init__()
        self.coroutine = coroutine

    def __repr__(self):
        return "Await(...)"


class Try(EffectBase):
    """Wrap a program to catch errors as Ok/Err results.

    yield Try(some_program) → Ok(value) or Err(error)
    """
    def __init__(self, program):
        super().__init__()
        self.program = program

    def __repr__(self):
        return f"Try({self.program!r})"


class HttpRequest(EffectBase):
    """HTTP request effect: dispatch a generic HTTP call.

    yield HttpRequest(method="GET", url="https://...") -> HttpResponse
    """

    def __init__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        body: bytes | str | dict[str, Any] | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        follow_redirects: bool = True,
    ) -> None:
        super().__init__()
        normalized_method = method.upper()
        if normalized_method not in _HTTP_METHODS:
            raise ValueError(f"Unsupported HTTP method: {method!r}")
        if max_retries < 0:
            raise ValueError(f"max_retries must be non-negative: {max_retries!r}")

        self.method = normalized_method
        self.url = url
        self.headers = headers
        self.params = params
        self.body = body
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.follow_redirects = follow_redirects

    def __repr__(self) -> str:
        return f"HttpRequest({self.method} {self.url!r})"


class HttpResponse:
    """Result of HttpRequest. Plain data — not an effect."""

    def __init__(
        self,
        status: int,
        headers: dict[str, str],
        content: bytes,
        text: str,
        url: str,
        elapsed_seconds: float,
    ) -> None:
        self.status = status
        self.headers = headers
        self.content = content
        self.text = text
        self.url = url
        self.elapsed_seconds = elapsed_seconds

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise HttpError(self.status, self.url, self.text[:500])


class HttpError(Exception):
    """Raised by HttpResponse.raise_for_status for HTTP error statuses."""

    def __init__(self, status: int, url: str, body_snippet: str) -> None:
        super().__init__(f"HTTP {status} {url}: {body_snippet}")
        self.status = status
        self.url = url
        self.body_snippet = body_snippet


class WriterTellEffect(EffectBase):
    """Writer/structured log effect: msg + kwargs.

    This is the wire type for slog() and Tell(). Listen collects these.
    """
    def __init__(self, msg, **kwargs):
        super().__init__()
        self.msg = msg
        self.kwargs = kwargs

    def __repr__(self):
        kw = ", ".join(f"{k}={v!r}" for k, v in self.kwargs.items())
        if kw:
            return f"slog({self.msg!r}, {kw})"
        return f"slog({self.msg!r})"


# Convenience alias
Slog = WriterTellEffect


def slog(msg, **kwargs):
    """Convenience function to create a WriterTellEffect."""
    return WriterTellEffect(msg, **kwargs)
