from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import httpx
import pytest
from doeff_core_effects.handlers import await_handler, slog_handler
from doeff_core_effects.http_handlers import http_fixture_handler, http_production_handler
from doeff_core_effects.scheduler import scheduled

from doeff import Pass, Resume, WithHandler, do, run


def _with_handlers(program: Any, *handlers: Any) -> Any:
    wrapped = program
    for handler in reversed(handlers):
        wrapped = WithHandler(handler, wrapped)
    return wrapped


@dataclass
class _FakeElapsed:
    seconds: float

    def total_seconds(self) -> float:
        return self.seconds


@dataclass
class _FakeResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes
    text: str
    url: str
    elapsed: _FakeElapsed


class _SlogCapture(Protocol):
    log: list[dict[str, Any]]


class _FakeAsyncClient:
    def __init__(self, responses: list[_FakeResponse | httpx.RequestError]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        url: Any,
        *,
        params: dict[str, Any] | None = None,
        content: Any = None,
        headers: Mapping[str, str | bytes] | None = None,
        cookies: Any = None,
        files: Any = None,
        auth: Any = None,
        timeout: float | None = None,
        follow_redirects: bool = True,
        proxies: Any = None,
        hooks: Any = None,
        stream: Any = None,
        verify: Any = None,
        cert: Any = None,
        json: Any = None,
    ) -> _FakeResponse:
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


async def _noop_sleep(_: float) -> None:
    return None


def _record_sleep(sleeps: list[float]):
    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    return sleep


def test_http_request_effect_shape_and_raise_for_status() -> None:
    from doeff_core_effects import HttpError, HttpRequest, HttpResponse

    request = HttpRequest("get", "https://example.test/data")
    assert request.method == "GET"
    assert repr(request) == "HttpRequest(GET 'https://example.test/data')"

    response = HttpResponse(
        status=404,
        headers={"Content-Type": "text/plain"},
        content=b"not found",
        text="not found",
        url="https://example.test/data",
        elapsed_seconds=0.5,
    )

    with pytest.raises(
        HttpError,
        match=re.escape("HTTP 404 https://example.test/data: not found"),
    ):
        response.raise_for_status()


def test_http_production_handler_get_and_slog() -> None:
    from doeff_core_effects import HttpRequest

    client = _FakeAsyncClient(
        [
            _FakeResponse(
                200, {"X-Test": "yes"}, b"ok", "ok", "https://example.test/final", _FakeElapsed(0.2)
            )
        ]
    )
    logs = cast(_SlogCapture, slog_handler())

    @do
    def body():
        return (yield HttpRequest("GET", "https://example.test/start", params={"a": "1"}))

    result = run(
        scheduled(
            _with_handlers(
                body(),
                logs,
                await_handler(),
                http_production_handler(client_factory=lambda: client, sleep=_noop_sleep),
            )
        )
    )

    assert result.status == 200
    assert result.headers == {"X-Test": "yes"}
    assert result.content == b"ok"
    assert result.text == "ok"
    assert result.url == "https://example.test/final"
    assert client.calls == [
        {
            "method": "GET",
            "url": "https://example.test/start",
            "headers": None,
            "params": {"a": "1"},
            "content": None,
            "timeout": 30.0,
            "follow_redirects": True,
        }
    ]
    assert logs.log == [
        {
            "msg": "http_request",
            "method": "GET",
            "url": "https://example.test/start",
            "status": 200,
            "final_url": "https://example.test/final",
            "elapsed_seconds": 0.2,
            "attempt": 1,
        }
    ]


def test_http_handlers_are_defhandler_functions(tmp_path: Path) -> None:
    production_handler = http_production_handler(
        client_factory=lambda: _FakeAsyncClient([]),
        sleep=_noop_sleep,
    )
    record_handler = http_fixture_handler(tmp_path / "http-fixture.pickle", mode="record")
    replay_handler = http_fixture_handler(tmp_path / "http-fixture.pickle", mode="replay")

    assert production_handler._doeff_is_handler_fn is True
    assert production_handler.__doeff_name__ == "_http-production-handler"
    assert record_handler._doeff_is_handler_fn is True
    assert record_handler.__doeff_name__ == "_http-fixture-record-handler"
    assert replay_handler._doeff_is_handler_fn is True
    assert replay_handler.__doeff_name__ == "_http-fixture-replay-handler"


def test_http_production_handler_post_json_body() -> None:
    from doeff_core_effects import HttpRequest

    client = _FakeAsyncClient(
        [
            _FakeResponse(
                201,
                {"Content-Type": "application/json"},
                b"{}",
                "{}",
                "https://example.test/api",
                _FakeElapsed(0.1),
            )
        ]
    )

    @do
    def body():
        return (
            yield HttpRequest(
                "POST",
                "https://example.test/api",
                headers={"X-Trace": "abc"},
                body={"b": 2, "a": 1},
            )
        )

    result = run(
        scheduled(
            _with_handlers(
                body(),
                slog_handler(),
                await_handler(),
                http_production_handler(client_factory=lambda: client, sleep=_noop_sleep),
            )
        )
    )

    assert result.status == 201
    assert client.calls[0]["headers"] == {
        "X-Trace": "abc",
        "Content-Type": "application/json",
    }
    assert client.calls[0]["content"] == b'{"a":1,"b":2}'


def test_http_production_handler_redirect_flag_and_timeout() -> None:
    from doeff_core_effects import HttpRequest

    client = _FakeAsyncClient(
        [
            _FakeResponse(
                302, {"Location": "/next"}, b"", "", "https://example.test/start", _FakeElapsed(0.1)
            )
        ]
    )

    @do
    def body():
        return (
            yield HttpRequest(
                "HEAD",
                "https://example.test/start",
                timeout_seconds=1.25,
                follow_redirects=False,
            )
        )

    result = run(
        scheduled(
            _with_handlers(
                body(),
                slog_handler(),
                await_handler(),
                http_production_handler(client_factory=lambda: client, sleep=_noop_sleep),
            )
        )
    )

    assert result.status == 302
    assert client.calls[0]["timeout"] == 1.25
    assert client.calls[0]["follow_redirects"] is False


def test_http_production_handler_retries_5xx_statuses() -> None:
    from doeff_core_effects import HttpRequest

    client = _FakeAsyncClient(
        [
            _FakeResponse(
                500, {}, b"error", "error", "https://example.test/api", _FakeElapsed(0.1)
            ),
            _FakeResponse(502, {}, b"bad", "bad", "https://example.test/api", _FakeElapsed(0.1)),
            _FakeResponse(200, {}, b"ok", "ok", "https://example.test/api", _FakeElapsed(0.1)),
        ]
    )
    sleeps: list[float] = []

    @do
    def body():
        return (yield HttpRequest("GET", "https://example.test/api", max_retries=2))

    result = run(
        scheduled(
            _with_handlers(
                body(),
                slog_handler(),
                await_handler(),
                http_production_handler(client_factory=lambda: client, sleep=_record_sleep(sleeps)),
            )
        )
    )

    assert result.status == 200
    assert len(client.calls) == 3
    assert sleeps == [0.25, 0.5]


def test_http_production_handler_retries_request_exceptions_with_timeout() -> None:
    from doeff_core_effects import HttpRequest

    client = _FakeAsyncClient(
        [
            httpx.TimeoutException("slow response"),
            _FakeResponse(200, {}, b"ok", "ok", "https://example.test/api", _FakeElapsed(0.1)),
        ]
    )
    sleeps: list[float] = []

    @do
    def body():
        return (
            yield HttpRequest(
                "GET",
                "https://example.test/api",
                timeout_seconds=0.01,
                max_retries=1,
            )
        )

    result = run(
        scheduled(
            _with_handlers(
                body(),
                slog_handler(),
                await_handler(),
                http_production_handler(client_factory=lambda: client, sleep=_record_sleep(sleeps)),
            )
        )
    )

    assert result.status == 200
    assert len(client.calls) == 2
    assert [call["timeout"] for call in client.calls] == [0.01, 0.01]
    assert sleeps == [0.25]


def test_http_fixture_handler_record_replay_round_trip(tmp_path: Path) -> None:
    from doeff_core_effects import HttpRequest, HttpResponse

    fixture_path = tmp_path / "http-fixture.pickle"
    response = HttpResponse(
        status=200,
        headers={"X-Fixture": "yes"},
        content=b"fixture",
        text="fixture",
        url="https://example.test/resource",
        elapsed_seconds=0.3,
    )
    calls = {"count": 0}

    @do
    def fake_transport(effect, k):
        if isinstance(effect, HttpRequest):
            calls["count"] += 1
            return (yield Resume(k, response))
        yield Pass(effect, k)

    @do
    def body():
        return (yield HttpRequest("GET", "https://example.test/resource"))

    recorded = run(
        _with_handlers(
            body(),
            fake_transport,
            http_fixture_handler(fixture_path, mode="record"),
        )
    )
    replayed = run(
        _with_handlers(
            body(),
            http_fixture_handler(fixture_path, mode="replay"),
        )
    )

    assert recorded.status == 200
    assert replayed.status == 200
    assert replayed.text == "fixture"
    assert calls["count"] == 1


def test_http_fixture_handler_replay_errors_on_unknown_request(tmp_path: Path) -> None:
    from doeff_core_effects import HttpRequest

    @do
    def body():
        return (yield HttpRequest("GET", "https://example.test/missing"))

    with pytest.raises(KeyError, match="No recorded HTTP fixture"):
        run(
            _with_handlers(
                body(),
                http_fixture_handler(tmp_path / "http-fixture.pickle", mode="replay"),
            )
        )
