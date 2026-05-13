from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class FakeElapsed:
    seconds: float

    def total_seconds(self) -> float:
        return self.seconds


@dataclass
class FakeResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes
    text: str
    url: str
    elapsed: FakeElapsed


class FakeAsyncClient:
    def __init__(self, responses: list[FakeResponse | httpx.RequestError]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.close_calls = 0

    async def request(
        self,
        method: str,
        url: Any,
        *,
        params: dict[str, Any] | None = None,
        content: Any = None,
        headers: Mapping[str, str | bytes] | None = None,
        timeout: float | None = None,
        follow_redirects: bool = True,
    ) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
                "content": content,
                "timeout": timeout,
                "follow_redirects": follow_redirects,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, httpx.RequestError):
            raise response
        return response

    async def aclose(self) -> None:
        self.close_calls += 1


async def noop_sleep(_: float) -> None:
    return None


def record_sleep(sleeps: list[float]):
    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    return sleep


def make_response(
    status: int,
    headers: dict[str, str] | None,
    content: bytes,
    text: str,
    url: str,
    elapsed_seconds: float,
) -> FakeResponse:
    return FakeResponse(
        status_code=status,
        headers={} if headers is None else headers,
        content=content,
        text=text,
        url=url,
        elapsed=FakeElapsed(elapsed_seconds),
    )


def timeout_error(message: str) -> httpx.TimeoutException:
    return httpx.TimeoutException(message)


def handler_log(handler: Any) -> list[dict[str, Any]]:
    return handler.log


def is_doeff_handler(handler: Any) -> bool:
    return handler._doeff_is_handler_fn is True


def handler_name(handler: Any) -> str:
    return handler.__doeff_name__
