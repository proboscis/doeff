from typing import Any

from doeff_vm import EffectBase

class HttpRequest(EffectBase):
    method: str
    url: str
    headers: dict[str, str] | None
    params: dict[str, Any] | None
    body: bytes | str | dict[str, Any] | None
    timeout_seconds: float
    max_retries: int
    follow_redirects: bool

    def __init__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        params: dict[str, Any] | None = ...,
        body: bytes | str | dict[str, Any] | None = ...,
        timeout_seconds: float = ...,
        max_retries: int = ...,
        follow_redirects: bool = ...,
    ) -> None: ...

    def __repr__(self) -> str: ...


class HttpResponse:
    status: int
    headers: dict[str, str]
    content: bytes
    text: str
    url: str
    elapsed_seconds: float

    def __init__(
        self,
        status: int,
        headers: dict[str, str],
        content: bytes,
        text: str,
        url: str,
        elapsed_seconds: float,
    ) -> None: ...

    def raise_for_status(self) -> None: ...


class HttpError(Exception):
    status: int
    url: str
    body_snippet: str

    def __init__(self, status: int, url: str, body_snippet: str) -> None: ...
