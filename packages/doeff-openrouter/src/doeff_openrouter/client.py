"""HTTP client and tracking helpers for OpenRouter."""


import copy
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from doeff import (
    Ask,
    EffectGenerator,
    Get,
    Put,
    Tell,
    Try,
    do,
)

from .types import APICallMetadata, CostInfo, TokenUsage


def _prepare_prompt_details(
    request_payload: dict[str, Any],
) -> tuple[dict[str, Any], str | None, list[dict[str, Any]], list[dict[str, Any]] | None]:
    """Return sanitized payload plus extracted text/images/messages."""

    sanitized_payload = copy.deepcopy(request_payload)

    messages = request_payload.get("messages")
    prompt_text_parts: list[str] = []
    prompt_images: list[dict[str, Any]] = []
    prompt_messages: list[dict[str, Any]] | None = None

    if isinstance(messages, list):
        sanitized_messages: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, dict):
                message_copy = copy.deepcopy(message)
                content = message.get("content")
                if isinstance(content, list):
                    content_copy: list[Any] = []
                    for part in content:
                        if isinstance(part, dict):
                            part_copy = copy.deepcopy(part)
                            part_type = part_copy.get("type")
                            if part_type == "text":
                                text_piece = part_copy.get("text")
                                if isinstance(text_piece, str):
                                    prompt_text_parts.append(text_piece)
                            elif part_type == "image_url":
                                image_url = part_copy.get("image_url", {})
                                if isinstance(image_url, dict):
                                    url = image_url.get("url")
                                    if isinstance(url, str):
                                        prompt_images.append({"data_uri": url})
                            content_copy.append(part_copy)
                        elif isinstance(part, str):
                            prompt_text_parts.append(part)
                            content_copy.append(part)
                        else:
                            content_copy.append(part)
                    message_copy["content"] = content_copy
                elif isinstance(content, str):
                    prompt_text_parts.append(content)
                sanitized_messages.append(message_copy)
            else:
                sanitized_messages.append(copy.deepcopy(message))
        sanitized_payload["messages"] = sanitized_messages
        prompt_messages = sanitized_messages

    prompt_text = "\n\n".join(filter(None, prompt_text_parts)).strip() or None

    return sanitized_payload, prompt_text, prompt_images, prompt_messages


DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass
class OpenRouterClient:
    """Thin wrapper around :class:`httpx.AsyncClient` configuration."""

    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    timeout: float | None = 60.0
    default_headers: dict[str, str] | None = None

    def build_headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        """Merge authorisation and default headers."""
        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.default_headers:
            headers.update(self.default_headers)
        if extra:
            headers.update(dict(extra))
        return headers

    async def a_chat_completions(
        self,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[dict[str, Any], Mapping[str, str]]:
        """Execute a chat completion request and return JSON response plus headers."""
        request_timeout = timeout if timeout is not None else self.timeout
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=request_timeout,
        ) as client:
            response = await client.post(
                "/chat/completions",
                json=payload,
                headers=self.build_headers(headers),
            )
            response.raise_for_status()
            return response.json(), response.headers


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


def extract_token_usage(response_data: dict[str, Any]) -> TokenUsage | None:
    """Extract token usage from OpenRouter JSON payload."""
    usage = response_data.get("usage")
    if not isinstance(usage, Mapping):
        return None
    prompt = _coerce_int(usage.get("prompt_tokens")) or _coerce_int(usage.get("input_tokens"))
    completion = _coerce_int(usage.get("completion_tokens")) or _coerce_int(
        usage.get("output_tokens")
    )
    total = _coerce_int(usage.get("total_tokens"))
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    reasoning_tokens = _coerce_int(usage.get("reasoning_tokens"))
    return TokenUsage(
        prompt_tokens=prompt or 0,
        completion_tokens=completion or 0,
        total_tokens=total or (prompt or 0) + (completion or 0),
        reasoning_tokens=reasoning_tokens,
    )


def extract_cost_info(response_data: dict[str, Any]) -> CostInfo | None:
    """Extract cost metadata when OpenRouter exposes it."""
    usage = response_data.get("usage")
    total_cost: float | None = None
    prompt_cost: float | None = None
    completion_cost: float | None = None
    currency = "USD"
    if isinstance(usage, Mapping):
        total_cost = (
            _coerce_float(usage.get("total_cost"))
            or _coerce_float(usage.get("estimated_cost"))
            or _coerce_float(usage.get("cost"))
        )
        prompt_cost = _coerce_float(usage.get("prompt_cost"))
        completion_cost = _coerce_float(usage.get("completion_cost"))
        currency = str(usage.get("currency", currency))
    provider_info = response_data.get("provider")
    if total_cost is None and isinstance(provider_info, Mapping):
        total_cost = _coerce_float(provider_info.get("estimated_cost"))
        currency = str(provider_info.get("currency", currency))
    if total_cost is None:
        return None
    return CostInfo(
        total_cost=total_cost,
        currency=currency,
        prompt_cost=prompt_cost,
        completion_cost=completion_cost,
    )


def extract_request_id(
    response_data: dict[str, Any] | None,
    headers: Mapping[str, str] | None,
) -> str | None:
    """Try multiple locations for the request identifier."""
    if response_data:
        for key in ("id", "request_id", "response_id"):
            value = response_data.get(key)
            if isinstance(value, str) and value:
                return value
    if headers:
        for key in ("x-request-id", "x-requestid", "openrouter-request-id"):
            if key in headers:
                return headers[key]
            upper = key.upper()
            if upper in headers:
                return headers[upper]
    return None


def extract_provider_name(response_data: dict[str, Any]) -> str | None:
    """Extract provider identifier from the response payload."""
    provider = response_data.get("provider")
    if isinstance(provider, Mapping):
        return str(provider.get("name") or provider.get("id"))
    if isinstance(provider, str):
        return provider
    return None


@do
def track_api_call(
    operation: str,
    model: str,
    request_payload: dict[str, Any],
    response_data: dict[str, Any] | None,
    response_headers: Mapping[str, str] | None,
    start_time: float,
    *,
    stream: bool = False,
    error: Exception | None = None,
    timestamp: datetime | None = None,
) -> EffectGenerator[APICallMetadata]:
    """Track OpenRouter API call in logs, graph, and state."""
    end_time = time.time()
    if timestamp is None:
        timestamp = datetime.fromtimestamp(end_time, tz=timezone.utc)
    latency_ms = (end_time - start_time) * 1000
    sanitized_payload, prompt_text, prompt_images, prompt_messages = _prepare_prompt_details(
        request_payload
    )
    token_usage = extract_token_usage(response_data) if response_data and not error else None
    cost_info = extract_cost_info(response_data) if response_data and not error else None
    request_id = extract_request_id(response_data, response_headers)
    provider = extract_provider_name(response_data) if response_data and not error else None

    metadata = APICallMetadata(
        operation=operation,
        model=model,
        timestamp=timestamp,
        request_id=request_id,
        latency_ms=latency_ms,
        token_usage=token_usage,
        cost_info=cost_info,
        error=str(error) if error else None,
        stream=stream,
        provider=provider,
    )

    if error:
        yield Tell(
            f"OpenRouter API error: operation={operation}, model={model}, error={error}, latency={latency_ms:.2f}ms"
        )
    else:
        log_msg = (
            f"OpenRouter API call: operation={operation}, model={model}, latency={latency_ms:.2f}ms"
        )
        if token_usage:
            log_msg += f", tokens={token_usage.total_tokens}"
        if cost_info:
            log_msg += f", cost={cost_info.total_cost:.6f} {cost_info.currency}"
        if provider:
            log_msg += f", provider={provider}"
        yield Tell(log_msg)

    graph_metadata = metadata.to_graph_metadata()
    request_summary = {
        "operation": operation,
        "model": model,
        "messages_count": len(sanitized_payload.get("messages", [])),
        "temperature": sanitized_payload.get("temperature"),
        "max_tokens": sanitized_payload.get("max_tokens")
        or sanitized_payload.get("max_completion_tokens"),
    }
    yield Tell(
        "OpenRouter request payload tracked: "
        f"timestamp={graph_metadata['timestamp']}, request={request_summary}"
    )
    yield Tell(
        "OpenRouter request payload details captured: "
        f"timestamp={graph_metadata['timestamp']}, payload_keys={sorted(sanitized_payload.keys())}"
    )

    if not error and response_data is not None:
        response_summary = {
            "success": True,
            "provider": provider,
            "finish_reason": None,
        }
        choices = response_data.get("choices") if isinstance(response_data, dict) else None
        if isinstance(choices, list) and choices:
            choice0 = choices[0]
            if isinstance(choice0, Mapping):
                response_summary["finish_reason"] = choice0.get("finish_reason")
        yield Tell(
            "OpenRouter response tracked: "
            f"timestamp={graph_metadata['timestamp']}, response={response_summary}"
        )
    elif error:
        yield Tell(
            "OpenRouter response error tracked: "
            f"timestamp={graph_metadata['timestamp']}, error={error}"
        )

    api_calls = yield Get("openrouter_api_calls")
    if api_calls is None:
        api_calls = []
    api_calls.append(
        {
            "operation": operation,
            "model": model,
            "timestamp": metadata.timestamp.isoformat(),
            "latency_ms": latency_ms,
            "error": str(error) if error else None,
            "tokens": {
                "prompt": token_usage.prompt_tokens if token_usage else 0,
                "completion": token_usage.completion_tokens if token_usage else 0,
                "total": token_usage.total_tokens if token_usage else 0,
            }
            if token_usage
            else None,
            "cost": cost_info.total_cost if cost_info else None,
            "provider": provider,
            "prompt_text": prompt_text,
            "prompt_images": prompt_images,
            "prompt_messages": prompt_messages,
        }
    )
    yield Put("openrouter_api_calls", api_calls)

    if cost_info:
        current_total = yield Get("total_openrouter_cost")
        new_total = (current_total or 0.0) + cost_info.total_cost
        yield Put("total_openrouter_cost", new_total)
        model_key = f"openrouter_cost_{model}"
        current_model_cost = yield Get(model_key)
        new_model_cost = (current_model_cost or 0.0) + cost_info.total_cost
        yield Put(model_key, new_model_cost)

    return metadata


@do
def get_openrouter_client() -> EffectGenerator[OpenRouterClient]:
    """Retrieve or create a cached :class:`OpenRouterClient` from the context."""

    @do
    def _ask_client() -> EffectGenerator[OpenRouterClient]:
        return (yield Ask("openrouter_client"))

    safe_client = yield Try(_ask_client())
    client = safe_client.value if safe_client.is_ok() else None
    if client:
        return client

    client = yield Get("openrouter_client")
    if client:
        return client

    @do
    def _ask(key: str) -> EffectGenerator[Any]:
        return (yield Ask(key))

    safe_api_key = yield Try(_ask("openrouter_api_key"))
    api_key = safe_api_key.value if safe_api_key.is_ok() else None
    if not api_key:
        api_key = yield Get("openrouter_api_key")

    safe_base_url = yield Try(_ask("openrouter_base_url"))
    base_url = safe_base_url.value if safe_base_url.is_ok() else None
    if not base_url:
        base_url = yield Get("openrouter_base_url")
    if not base_url:
        base_url = DEFAULT_BASE_URL

    safe_timeout = yield Try(_ask("openrouter_timeout"))
    timeout_value = safe_timeout.value if safe_timeout.is_ok() else None
    if timeout_value is None:
        timeout_value = yield Get("openrouter_timeout")
    timeout: float | None
    try:
        timeout = float(timeout_value) if timeout_value is not None else None
    except (TypeError, ValueError):  # pragma: no cover - defensive
        timeout = None

    safe_headers = yield Try(_ask("openrouter_default_headers"))
    default_headers = safe_headers.value if safe_headers.is_ok() else None
    if default_headers is None:
        default_headers = yield Get("openrouter_default_headers")
    if default_headers is not None and not isinstance(default_headers, Mapping):
        default_headers = dict(default_headers)

    client = OpenRouterClient(
        api_key=api_key,
        base_url=str(base_url),
        timeout=timeout,
        default_headers=dict(default_headers) if default_headers else None,
    )
    yield Put("openrouter_client", client)
    return client


@do
def get_total_cost() -> EffectGenerator[float]:
    """Return accumulated OpenRouter cost from the state."""
    total_cost = yield Get("total_openrouter_cost")
    return float(total_cost or 0.0)


@do
def get_model_cost(model: str) -> EffectGenerator[float]:
    """Return accumulated cost for a specific model."""
    cost = yield Get(f"openrouter_cost_{model}")
    return float(cost or 0.0)


@do
def reset_cost_tracking() -> EffectGenerator[None]:
    """Reset tracked OpenRouter costs."""
    yield Put("total_openrouter_cost", 0.0)
    yield Tell("Reset OpenRouter cost tracking")
    return None


__all__ = [
    "OpenRouterClient",
    "extract_cost_info",
    "extract_token_usage",
    "get_model_cost",
    "get_openrouter_client",
    "get_total_cost",
    "reset_cost_tracking",
    "track_api_call",
]
