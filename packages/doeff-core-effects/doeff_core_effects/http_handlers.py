"""HTTP handlers for doeff-core-effects."""

from __future__ import annotations

import hashlib
import json
import pickle
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

from doeff import do
from doeff.program import Pass, Resume
from doeff_core_effects.effects import Await, HttpRequest, HttpResponse, slog

FixtureMode = Literal["record", "replay"]
SleepFn = Callable[[float], Awaitable[None]]


class HttpAsyncClient(Protocol):
    async def request(
        self,
        method: str,
        url: Any,
        *,
        params: Any = None,
        content: Any = None,
        headers: Any = None,
        timeout: Any = None,
        follow_redirects: bool = True,
    ) -> Any: ...


AsyncClientFactory = Callable[[], HttpAsyncClient]


def _default_client_factory() -> HttpAsyncClient:
    return httpx.AsyncClient()


async def _asyncio_sleep(delay: float) -> None:
    import asyncio

    await asyncio.sleep(delay)


def http_production_handler(
    *,
    client_factory: AsyncClientFactory = _default_client_factory,
    sleep: SleepFn = _asyncio_sleep,
):
    """Handle HttpRequest with a single async HTTP client and retry/backoff."""

    client = client_factory()

    @do
    def handler(effect, k):
        if not isinstance(effect, HttpRequest):
            yield Pass(effect, k)
            return

        for attempt_index in range(effect.max_retries + 1):
            try:
                response = yield Await(_perform_request_once(client, effect))
            except httpx.RequestError:
                if attempt_index == effect.max_retries:
                    raise
                yield Await(sleep(_retry_delay_seconds(attempt_index)))
                continue

            yield slog(
                "http_request",
                method=effect.method,
                url=effect.url,
                status=response.status,
                final_url=response.url,
                elapsed_seconds=response.elapsed_seconds,
                attempt=attempt_index + 1,
            )

            if response.status >= 500 and attempt_index < effect.max_retries:
                yield Await(sleep(_retry_delay_seconds(attempt_index)))
                continue

            result = yield Resume(k, response)
            return result

        raise AssertionError("unreachable HttpRequest retry state")

    return handler


def http_fixture_handler(fixture_path: str | Path, *, mode: FixtureMode):
    """Record or replay HttpRequest responses from a pickle fixture file."""

    if mode not in ("record", "replay"):
        raise ValueError(f"Unsupported HTTP fixture mode: {mode!r}")

    path = Path(fixture_path)
    fixtures = _load_fixtures(path)

    @do
    def handler(effect, k):
        if not isinstance(effect, HttpRequest):
            yield Pass(effect, k)
            return

        key = _fixture_key(effect)
        if mode == "replay":
            if key not in fixtures:
                raise KeyError(f"No recorded HTTP fixture for {effect!r}")
            response = _response_from_record(fixtures[key])
            result = yield Resume(k, response)
            return result

        response = yield effect
        if not isinstance(response, HttpResponse):
            raise TypeError(f"HttpRequest fixture recorder received non-HttpResponse: {response!r}")
        fixtures[key] = _response_to_record(response)
        _write_fixtures(path, fixtures)
        result = yield Resume(k, response)
        return result

    return handler


async def _perform_request_once(client: HttpAsyncClient, request: HttpRequest) -> HttpResponse:
    headers, content = _request_headers_and_content(request)
    response = await client.request(
        method=request.method,
        url=request.url,
        headers=headers,
        params=request.params,
        content=content,
        timeout=request.timeout_seconds,
        follow_redirects=request.follow_redirects,
    )
    return HttpResponse(
        status=response.status_code,
        headers=dict(response.headers),
        content=response.content,
        text=response.text,
        url=str(response.url),
        elapsed_seconds=response.elapsed.total_seconds(),
    )


def _request_headers_and_content(
    request: HttpRequest,
) -> tuple[dict[str, str] | None, bytes | None]:
    headers = dict(request.headers) if request.headers is not None else None
    body = request.body

    if body is None:
        return headers, None

    if isinstance(body, bytes):
        return headers, body

    if isinstance(body, str):
        return headers, body.encode("utf-8")

    data = _json_body_bytes(body)
    if headers is None:
        headers = {"Content-Type": "application/json"}
    elif not _has_header(headers, "Content-Type"):
        headers["Content-Type"] = "application/json"
    return headers, data


def _has_header(headers: dict[str, str], header_name: str) -> bool:
    target = header_name.lower()
    return any(name.lower() == target for name in headers)


def _json_body_bytes(body: dict[str, Any]) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _retry_delay_seconds(attempt_index: int) -> float:
    return 0.25 * (2**attempt_index)


def _fixture_key(request: HttpRequest) -> str:
    payload = {
        "method": request.method,
        "url": request.url,
        "params": _sorted_mapping(request.params),
        "body_sha256": _body_sha256(request.body),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sorted_mapping(mapping: dict[str, Any] | None) -> list[tuple[str, Any]] | None:
    if mapping is None:
        return None
    return sorted(mapping.items())


def _body_sha256(body: bytes | str | dict[str, Any] | None) -> str | None:
    if body is None:
        return None
    if isinstance(body, bytes):
        data = body
    elif isinstance(body, str):
        data = body.encode("utf-8")
    else:
        data = _json_body_bytes(body)
    return hashlib.sha256(data).hexdigest()


def _load_fixtures(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open("rb") as fixture_file:
        return pickle.load(fixture_file)


def _write_fixtures(path: Path, fixtures: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fixture_file:
        pickle.dump(fixtures, fixture_file)


def _response_to_record(response: HttpResponse) -> dict[str, Any]:
    return {
        "status": response.status,
        "headers": response.headers,
        "content": response.content,
        "text": response.text,
        "url": response.url,
        "elapsed_seconds": response.elapsed_seconds,
    }


def _response_from_record(record: dict[str, Any]) -> HttpResponse:
    return HttpResponse(
        status=record["status"],
        headers=record["headers"],
        content=record["content"],
        text=record["text"],
        url=record["url"],
        elapsed_seconds=record["elapsed_seconds"],
    )
