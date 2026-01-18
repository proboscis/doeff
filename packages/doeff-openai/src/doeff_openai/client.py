"""Main OpenAI client using doeff effects for observability."""

import copy
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from openai import AsyncOpenAI, OpenAI
from openai.types import CreateEmbeddingResponse
from openai.types.chat import ChatCompletion

from doeff import (
    Ask,
    AtomicUpdate,
    EffectGenerator,
    Get,
    Log,
    Put,
    Step,
    do,
)
from doeff_openai.costs import calculate_cost
from doeff_openai.types import APICallMetadata, TokenUsage


def _prepare_prompt_details(
    request_payload: dict[str, Any]
) -> tuple[dict[str, Any], str | None, list[dict[str, Any]], list[dict[str, Any]] | None]:
    """Create a sanitized payload and extract prompt text/images/messages."""

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
                                        prompt_images.append(
                                            {
                                                "data_uri": url,
                                                "detail": image_url.get("detail"),
                                            }
                                        )
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


@dataclass
class ClientHolder:
    """Holds both sync and async OpenAI clients."""
    sync: OpenAI | None = None
    async_: AsyncOpenAI | None = None


class OpenAIClient:
    """OpenAI client with Effect-based observability."""

    def __init__(
        self,
        api_key: str | None = None,
        organization: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ):
        """Initialize OpenAI client."""
        import openai

        self.api_key = api_key
        self.organization = organization
        self.base_url = base_url
        self.timeout = timeout
        # Use OpenAI's default if not specified
        self.max_retries = max_retries if max_retries is not None else openai.DEFAULT_MAX_RETRIES

        # Single mutable attribute for both clients
        self._mut_clients = ClientHolder()

    @property
    def sync_client(self) -> OpenAI:
        """Get or create sync client."""
        if self._mut_clients.sync is None:
            self._mut_clients.sync = OpenAI(
                api_key=self.api_key,
                organization=self.organization,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=self.max_retries,
            )
        return self._mut_clients.sync

    @property
    def async_client(self) -> AsyncOpenAI:
        """Get or create async client."""
        if self._mut_clients.async_ is None:
            self._mut_clients.async_ = AsyncOpenAI(
                api_key=self.api_key,
                organization=self.organization,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=self.max_retries,
            )
        return self._mut_clients.async_


@do
def get_openai_client() -> EffectGenerator[OpenAIClient]:
    """Get OpenAI client from environment or create new one."""
    from doeff import Safe, do

    # Create a program that asks for the client
    @do
    def try_ask_client():
        return (yield Ask("openai_client"))

    # Use Safe to handle KeyError
    safe_result = yield Safe(try_ask_client())
    client = safe_result.value if safe_result.is_ok() else None
    if client:
        return client

    # Try to get from State
    client = yield Get("openai_client")
    if client:
        return client

    # Get API key from Reader environment or State
    # Try to get from Reader environment
    @do
    def try_ask_api_key():
        return (yield Ask("openai_api_key"))

    safe_api_key = yield Safe(try_ask_api_key())
    api_key = safe_api_key.value if safe_api_key.is_ok() else None

    # If not found, try State
    if not api_key:
        api_key = yield Get("openai_api_key")

    # Create client (api_key might be None, which is ok - OpenAI client will use its own defaults)
    client = OpenAIClient(api_key=api_key)

    # Store in state for reuse
    yield Put("openai_client", client)

    return client


def extract_token_usage(response: ChatCompletion | CreateEmbeddingResponse) -> TokenUsage | None:
    """Extract token usage from OpenAI response."""
    if hasattr(response, "usage") and response.usage:
        usage = response.usage
        return TokenUsage(
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            total_tokens=getattr(usage, "total_tokens", 0),
        )
    return None


def extract_request_id(response: Any) -> str | None:
    """Extract request ID from OpenAI response."""
    # Try different attributes where request ID might be
    for attr in ["id", "request_id", "_request_id"]:
        if hasattr(response, attr):
            return getattr(response, attr)

    # Check in headers if available
    if hasattr(response, "_headers"):
        headers = response._headers
        if isinstance(headers, dict):
            return headers.get("x-request-id")

    return None


@do
def track_api_call(
    operation: str,
    model: str,
    request_payload: dict[str, Any],
    response: Any,
    start_time: float,
    error: Exception | None = None,
) -> EffectGenerator[APICallMetadata]:
    """Track an API call with Graph and Log effects."""
    end_time = time.time()
    latency_ms = (end_time - start_time) * 1000

    sanitized_payload, prompt_text, prompt_images, prompt_messages = _prepare_prompt_details(
        request_payload
    )

    # Extract token usage if available
    token_usage = None
    cost_info = None
    if response and not error:
        token_usage = extract_token_usage(response)
        if token_usage:
            cost_info = calculate_cost(model, token_usage)

    # Extract request ID
    request_id = extract_request_id(response) if response else None

    # Create metadata
    metadata = APICallMetadata(
        operation=operation,
        model=model,
        timestamp=datetime.now(timezone.utc),
        request_id=request_id,
        latency_ms=latency_ms,
        token_usage=token_usage,
        cost_info=cost_info,
        error=str(error) if error else None,
        stream=sanitized_payload.get("stream", False),
    )

    # Log the API call
    if error:
        yield Tell(f"OpenAI API error: operation={operation}, model={model}, error={error!s}, latency={latency_ms:.2f}ms")
    else:
        log_msg = f"OpenAI API call: operation={operation}, model={model}, latency={latency_ms:.2f}ms"
        if token_usage:
            log_msg += f", tokens={token_usage.total_tokens}"
        if cost_info:
            log_msg += f", cost=${cost_info.total_cost:.6f}"
        yield Tell(log_msg)

    # Create Graph step with full metadata
    graph_metadata = metadata.to_graph_metadata()

    # Include request summary in graph (not full request to avoid bloat)
    request_summary = {
        "operation": operation,
        "model": model,
    }
    if operation == "chat.completion":
        request_summary["messages_count"] = len(sanitized_payload.get("messages", []))
        request_summary["temperature"] = sanitized_payload.get("temperature")
        request_summary["max_tokens"] = sanitized_payload.get("max_tokens")
    elif operation == "embedding":
        input_data = sanitized_payload.get("input", "")
        if isinstance(input_data, list):
            request_summary["input_count"] = len(input_data)
        else:
            request_summary["input_length"] = len(input_data)

    # Add request as graph step
    yield Step(
        {"request_payload": sanitized_payload, "timestamp": graph_metadata["timestamp"]},
        {**graph_metadata, "phase": "request_payload"}
    )
    yield Step(
        {"request": request_summary, "timestamp": graph_metadata["timestamp"]},
        {**graph_metadata, "phase": "request"}
    )

    # Add response as graph step
    if not error:
        response_summary = {
            "success": True,
            "model": model,
        }
        if operation == "chat.completion" and hasattr(response, "choices"):
            response_summary["finish_reason"] = response.choices[0].finish_reason if response.choices else None

        yield Step(
            {"response": response_summary, "timestamp": graph_metadata["timestamp"]},
            {**graph_metadata, "phase": "response"}
        )
    else:
        yield Step(
            {"error": str(error), "timestamp": graph_metadata["timestamp"]},
            {**graph_metadata, "phase": "error"}
        )

    # Track API call in state using AtomicUpdate for thread-safe parallel execution
    call_entry = {
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
        "prompt_text": prompt_text,
        "prompt_images": prompt_images,
        "prompt_messages": prompt_messages,
    }

    def _append_call(current: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        entries = list(current) if current else []
        entries.append(call_entry)
        return entries

    yield AtomicUpdate("openai_api_calls", _append_call, default_factory=list)

    # Track cumulative cost in state using AtomicUpdate for thread-safe parallel execution
    if cost_info:
        def _increment_total(current: float | None) -> float:
            return (current or 0.0) + cost_info.total_cost

        yield AtomicUpdate(
            "total_openai_cost",
            _increment_total,
            default_factory=lambda: 0.0,
        )

        # Also track per-model costs
        model_cost_key = f"openai_cost_{model}"

        def _increment_model(current: float | None) -> float:
            return (current or 0.0) + cost_info.total_cost

        yield AtomicUpdate(
            model_cost_key,
            _increment_model,
            default_factory=lambda: 0.0,
        )

    return metadata


@do
def get_total_cost() -> EffectGenerator[float]:
    """Get the total accumulated OpenAI API cost."""
    total_cost = yield Get("total_openai_cost")
    return total_cost or 0.0


@do
def get_model_cost(model: str) -> EffectGenerator[float]:
    """Get the accumulated cost for a specific model."""
    model_cost = yield Get(f"openai_cost_{model}")
    return model_cost or 0.0


@do
def reset_cost_tracking() -> EffectGenerator[None]:
    """Reset all cost tracking state."""
    yield Put("total_openai_cost", 0.0)
    yield Tell("Reset OpenAI cost tracking")
    return None
