from typing import Any


def http_get(
    url: str,
    *,
    headers: dict[str, str] | None = ...,
    params: dict[str, Any] | None = ...,
    timeout_seconds: float = ...,
    max_retries: int = ...,
    follow_redirects: bool = ...,
) -> Any: ...


def http_post(
    url: str,
    *,
    headers: dict[str, str] | None = ...,
    params: dict[str, Any] | None = ...,
    body: bytes | str | dict[str, Any] | None = ...,
    timeout_seconds: float = ...,
    max_retries: int = ...,
    follow_redirects: bool = ...,
) -> Any: ...


def http_put(
    url: str,
    *,
    headers: dict[str, str] | None = ...,
    params: dict[str, Any] | None = ...,
    body: bytes | str | dict[str, Any] | None = ...,
    timeout_seconds: float = ...,
    max_retries: int = ...,
    follow_redirects: bool = ...,
) -> Any: ...


def http_delete(
    url: str,
    *,
    headers: dict[str, str] | None = ...,
    params: dict[str, Any] | None = ...,
    timeout_seconds: float = ...,
    max_retries: int = ...,
    follow_redirects: bool = ...,
) -> Any: ...


def http_head(
    url: str,
    *,
    headers: dict[str, str] | None = ...,
    params: dict[str, Any] | None = ...,
    timeout_seconds: float = ...,
    max_retries: int = ...,
    follow_redirects: bool = ...,
) -> Any: ...
