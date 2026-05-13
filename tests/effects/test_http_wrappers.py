from __future__ import annotations

from typing import Any

import doeff_hy  # noqa: F401  — registers Hy import hooks
from doeff_core_effects import HttpRequest, HttpResponse

from doeff import Pass, Resume, WithHandler, do, run


def _capture_http_request_handler(captured: list[HttpRequest]):
    response = HttpResponse(
        status=200,
        headers={},
        content=b"ok",
        text="ok",
        url="https://example.test/final",
        elapsed_seconds=0.0,
    )

    @do
    def handler(effect, k):
        if isinstance(effect, HttpRequest):
            captured.append(effect)
            return (yield Resume(k, response))
        yield Pass(effect, k)

    return handler


def _capture_request(program: Any) -> HttpRequest:
    captured: list[HttpRequest] = []
    result = run(WithHandler(_capture_http_request_handler(captured), program))
    assert isinstance(result, HttpResponse)
    assert len(captured) == 1
    return captured[0]


def test_http_get_wrapper_builds_request() -> None:
    from doeff_hy.http import http_get

    request = _capture_request(http_get("https://example.test/data", params={"a": "1"}))

    assert request.method == "GET"
    assert request.url == "https://example.test/data"
    assert request.params == {"a": "1"}


def test_http_post_wrapper_builds_json_request() -> None:
    from doeff_hy.http import http_post

    request = _capture_request(http_post("https://example.test/data", body={"a": 1}))

    assert request.method == "POST"
    assert request.body == {"a": 1}


def test_http_put_delete_head_wrappers_build_methods() -> None:
    from doeff_hy.http import http_delete, http_head, http_put

    put_request = _capture_request(http_put("https://example.test/data", body="payload"))
    delete_request = _capture_request(http_delete("https://example.test/data"))
    head_request = _capture_request(http_head("https://example.test/data"))

    assert put_request.method == "PUT"
    assert delete_request.method == "DELETE"
    assert head_request.method == "HEAD"
