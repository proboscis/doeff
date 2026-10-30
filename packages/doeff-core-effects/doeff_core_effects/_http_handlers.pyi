import ssl
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

FixtureMode = Literal["record", "replay"]
SleepFn = Callable[[float], Awaitable[None]]

CLIENT_KEEPALIVE_SECONDS: float


class HttpAsyncClient(Protocol):
    def request(
        self,
        method: str,
        url: Any,
        *,
        params: Any = ...,
        content: Any = ...,
        headers: Any = ...,
        timeout: Any = ...,
        follow_redirects: bool = ...,
    ) -> Awaitable[Any]: ...

    def aclose(self) -> Awaitable[None]: ...


AsyncClientFactory = Callable[[], HttpAsyncClient]


def http_client_factory(
    *,
    trust_env: bool = ...,
    verify: ssl.SSLContext | bool = ...,
) -> httpx.AsyncClient: ...


def http_production_handler(
    *,
    client_factory: AsyncClientFactory = ...,
    sleep: SleepFn = ...,
) -> Any: ...


def http_fixture_handler(
    fixture_path: str | Path,
    *,
    mode: FixtureMode,
    client_factory: AsyncClientFactory = ...,
    sleep: SleepFn = ...,
) -> Any: ...
