from __future__ import annotations

import re
from pathlib import Path

import pytest
from doeff_core_effects.http_handlers import http_fixture_handler, http_production_handler

from tests.effects.http_request_support import (
    FakeAsyncClient,
    handler_name,
    is_doeff_handler,
    noop_sleep,
)


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


def test_http_handlers_are_defhandler_functions(tmp_path: Path) -> None:
    production_handler = http_production_handler(
        client_factory=lambda: FakeAsyncClient([]),
        sleep=noop_sleep,
    )
    record_handler = http_fixture_handler(tmp_path / "http-fixture.pickle", mode="record")
    replay_handler = http_fixture_handler(tmp_path / "http-fixture.pickle", mode="replay")

    assert is_doeff_handler(production_handler)
    assert handler_name(production_handler) == "_http-production-handler"
    assert is_doeff_handler(record_handler)
    assert handler_name(record_handler) == "_http-fixture-record-handler"
    assert is_doeff_handler(replay_handler)
    assert handler_name(replay_handler) == "_http-fixture-replay-handler"
