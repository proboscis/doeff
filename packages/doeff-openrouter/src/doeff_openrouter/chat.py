"""Chat completion helpers with doeff-powered observability."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from doeff import (
    Await,
    Delay,
    EffectGenerator,
    Safe,
    Tell,
    do,
)

from .client import get_openrouter_client, track_api_call


class OpenRouterResponseError(RuntimeError):
    """Raised when OpenRouter returns an error payload."""

    def __init__(self, payload: dict[str, Any]):
        message = payload.get("error")
        if isinstance(message, dict):
            message = message.get("message") or message.get("code")
        message = message or "Unexpected OpenRouter error"
        super().__init__(str(message))
        self.payload = payload


def _requires_max_completion_tokens(model: str) -> bool:
    model_lower = model.lower()
    return (
        "gpt-5" in model_lower
        or "gpt5" in model_lower
        or model_lower.startswith("o1")
        or model_lower.startswith("o3")
        or model_lower.startswith("o4")
    )


@do
def chat_completion(
    messages: list[dict[str, Any]],
    model: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    provider: dict[str, Any] | None = None,
    include_reasoning: bool = False,
    reasoning: dict[str, Any] | None = None,
    stream: bool = False,
    extra_headers: Mapping[str, str] | None = None,
    request_timeout: float | None = None,
    **kwargs: Any,
) -> EffectGenerator[dict[str, Any]]:
    """Invoke OpenRouter chat completions with retries and observability."""
    if stream:
        yield Tell("Streaming mode for OpenRouter is not implemented yet")
        raise NotImplementedError("Streaming responses are not supported")

    yield Tell(
        f"OpenRouter chat request: model={model}, messages={len(messages)}, include_reasoning={include_reasoning}"
    )

    request_data: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }

    if temperature is not None:
        request_data["temperature"] = temperature
    if top_p is not None:
        request_data["top_p"] = top_p
    if provider is not None:
        request_data["provider"] = provider
    if include_reasoning:
        request_data["include_reasoning"] = True
    if reasoning is not None:
        request_data["reasoning"] = reasoning

    if max_tokens is not None:
        token_key = "max_completion_tokens" if _requires_max_completion_tokens(model) else "max_tokens"
        request_data[token_key] = max_tokens

    request_data.update(kwargs)

    client = yield get_openrouter_client()

    # Retry transient network errors up to three times by default
    max_attempts = 3
    delay_seconds = 1.0
    last_exception: BaseException | None = None

    for attempt in range(max_attempts):
        start_time = time.time()

        @do
        def perform() -> EffectGenerator[dict[str, Any]]:
            response_data, response_headers = yield Await(
                client.a_chat_completions(
                    request_data,
                    timeout=request_timeout,
                    headers=extra_headers,
                )
            )
            if isinstance(response_data, dict) and response_data.get("error"):
                error = OpenRouterResponseError(response_data)
                yield track_api_call(
                    operation="chat.completion",
                    model=model,
                    request_payload=request_data,
                    response_data=response_data,
                    response_headers=response_headers,
                    start_time=start_time,
                    stream=False,
                    error=error,
                )
                raise error
            yield track_api_call(
                operation="chat.completion",
                model=model,
                request_payload=request_data,
                response_data=response_data,
                response_headers=response_headers,
                start_time=start_time,
                stream=False,
                error=None,
            )
            return response_data

        safe_result = yield Safe(perform())
        if safe_result.is_ok():
            return safe_result.value

        exc = safe_result.error
        last_exception = exc

        if isinstance(exc, OpenRouterResponseError):
            yield Tell(f"OpenRouter responded with an error payload: {exc}")
            raise exc

        yield Tell(f"OpenRouter request raised {exc.__class__.__name__}: {exc}")
        yield track_api_call(
            operation="chat.completion",
            model=model,
            request_payload=request_data,
            response_data=None,
            response_headers=None,
            start_time=start_time,
            stream=False,
            error=exc,
        )

        if attempt < max_attempts - 1:
            yield Tell(f"Retrying in {delay_seconds}s (attempt {attempt + 2}/{max_attempts})")
            yield Delay(delay_seconds)

    # All retries exhausted
    if last_exception is not None:
        raise last_exception
    raise RuntimeError("All retry attempts failed")


__all__ = ["OpenRouterResponseError", "chat_completion"]
