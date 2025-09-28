"""Seedream client helpers integrated with doeff effects."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from doeff import (
    Ask,
    Catch,
    EffectGenerator,
    Fail,
    Get,
    Log,
    Put,
    Step,
    do,
)

DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_TIMEOUT = 60.0


def _summarize_data_uri(value: Any) -> Any:
    """Return a readable representation for base64 payloads."""

    if isinstance(value, str) and value.startswith("data:image") and ";base64," in value:
        header, data = value.split(";base64,", 1)
        return f"{header};base64,<len={len(data)}>"
    if isinstance(value, list):
        return [_summarize_data_uri(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_summarize_data_uri(item) for item in value)
    return value


def _sanitize_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Produce a sanitized copy safe for logging and telemetry."""

    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "image":
            sanitized[key] = _summarize_data_uri(value)
        else:
            sanitized[key] = value
    return sanitized


@dataclass(slots=True)
class SeedreamClient:
    """Thin wrapper around the Ark Seedream image-generation endpoint.

    The client is intentionally lightweight: it only knows how to POST JSON
    payloads to ``/images/generations`` and leaves retry, logging, and
    higher-level orchestration to ``structured_llm``. Instances can be reused
    safely across requests – every method is stateless and builds up the
    payload for each call.
    """

    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    timeout: float | None = DEFAULT_TIMEOUT
    default_headers: Mapping[str, str] | None = None

    def build_headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        """Compose the HTTP headers for an outgoing request."""

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.default_headers:
            headers.update(dict(self.default_headers))
        if self.api_key and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if extra:
            headers.update(dict(extra))
        return headers

    async def a_generate_images(
        self,
        payload: Mapping[str, Any],
        *,
        timeout: float | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute an image generation request against the Seedream API.

        Parameters
        ----------
        payload:
            JSON-serialisable mapping describing the request body expected by
            ``POST /images/generations``. The helper functions in
            ``structured_llm`` take care of building a compliant payload.
        timeout:
            Optional request timeout (seconds). Falls back to the instance
            default when omitted.
        headers:
            Additional HTTP headers to merge on top of the automatic
            ``Content-Type`` and ``Authorization`` entries.

        Returns
        -------
        dict[str, Any]
            Parsed JSON response from the Ark API.
        """

        request_timeout = timeout if timeout is not None else self.timeout
        request_headers = self.build_headers(headers)
        if "Authorization" not in request_headers:
            raise ValueError("Seedream API key missing; provide seedream_api_key or an Authorization header")
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=request_timeout,
        ) as client:
            response = await client.post(
                "/images/generations",
                json=dict(payload),
                headers=request_headers,
            )
            response.raise_for_status()
            return response.json()


@do
def get_seedream_client() -> EffectGenerator[SeedreamClient]:
    """Retrieve or construct a :class:`SeedreamClient` via Reader/State effects.

    The resolver checks the following keys (in order) before falling back to
    constructing a new client:

    - ``seedream_client`` – expected to be a ready-to-use :class:`SeedreamClient`
      instance.
    - ``seedream_api_key`` – string API key used to authorise requests.
    - ``seedream_base_url`` – override for self-hosted or staging Ark endpoints.
    - ``seedream_default_headers`` – additional HTTP headers to merge into each
      request (for example to set ``X-Volc-Region``).
    """

    def ask_optional(name: str) -> EffectGenerator[Any]:
        return Catch(Ask(name), lambda exc: None if isinstance(exc, KeyError) else None)  # type: ignore[return-value]

    candidate = yield Catch(Ask("seedream_client"), lambda exc: None if isinstance(exc, KeyError) else None)
    if isinstance(candidate, SeedreamClient):
        return candidate

    state_client = yield Get("seedream_client")
    if isinstance(state_client, SeedreamClient):
        return state_client

    api_key = yield ask_optional("seedream_api_key")
    if api_key is None:
        api_key = yield Get("seedream_api_key")

    base_url = yield ask_optional("seedream_base_url")
    if base_url is None:
        base_url = yield Get("seedream_base_url")

    default_headers = yield ask_optional("seedream_default_headers")
    if default_headers is None:
        default_headers = yield Get("seedream_default_headers")

    if default_headers is not None and not isinstance(default_headers, Mapping):
        yield Log("Ignoring seedream_default_headers because it is not a mapping")
        default_headers = None

    client = SeedreamClient(
        api_key=api_key,
        base_url=base_url or DEFAULT_BASE_URL,
        default_headers=default_headers,
    )

    yield Put("seedream_client", client)
    return client


@do
def track_api_call(
    *,
    operation: str,
    model: str,
    request_payload: Mapping[str, Any],
    response: Mapping[str, Any] | None,
    start_time: float,
    error: Exception | None = None,
) -> EffectGenerator[None]:
    """Emit observability breadcrumbs for a Seedream API call.

    Parameters
    ----------
    operation:
        Short string describing the API action (``"images.generate"``).
    model:
        Model identifier that was requested.
    request_payload:
        Payload submitted to the Ark endpoint. Sensitive fields such as inline
        images are sanitised before logging.
    response:
        Parsed JSON response, or ``None`` when the request raised an exception.
    start_time:
        Timestamp captured immediately before issuing the HTTP call. Used to
        compute the latency for the log entry.
    error:
        Optional exception instance when the call failed.
    """

    end_time = time.time()
    latency_ms = (end_time - start_time) * 1000

    value: dict[str, Any] = {
        "operation": operation,
        "model": model,
        "status": "error" if error else "success",
    }

    meta: dict[str, Any] = {
        "latency_ms": latency_ms,
        "request": _sanitize_payload(request_payload),
    }

    if response:
        usage = response.get("usage") if isinstance(response, Mapping) else None
        if isinstance(usage, Mapping):
            generated_images = usage.get("generated_images")
            if generated_images is not None:
                value["generated_images"] = generated_images
            output_tokens = usage.get("output_tokens")
            if output_tokens is not None:
                value["output_tokens"] = output_tokens
        meta["response_keys"] = sorted(response.keys())

    if error:
        meta["error"] = repr(error)

    yield Log(
        "Seedream %s %s in %.0f ms" % (
            operation,
            "failed" if error else "succeeded",
            latency_ms,
        )
    )
    yield Step(value=value, meta=meta)


__all__ = ["SeedreamClient", "DEFAULT_BASE_URL", "DEFAULT_TIMEOUT", "get_seedream_client", "track_api_call"]
