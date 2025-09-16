"""Main OpenAI client using doeff effects for observability."""

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

from openai import AsyncOpenAI, OpenAI
from openai.types.chat import ChatCompletion
from openai.types import CreateEmbeddingResponse

from doeff import (
    Program,
    do,
    Effect,
    EffectGenerator,
    Log,
    Step,
    Put,
    Get,
    IO,
    Ask,
    Await,
)

from doeff_openai.types import (
    APICallMetadata,
    TokenUsage,
    CostInfo,
)
from doeff_openai.costs import calculate_cost


@dataclass
class ClientHolder:
    """Holds both sync and async OpenAI clients."""
    sync: Optional[OpenAI] = None
    async_: Optional[AsyncOpenAI] = None


class OpenAIClient:
    """OpenAI client with Effect-based observability."""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        organization: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
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
    # Try to get from Reader environment first
    from doeff import Catch, do
    
    # Create a program that asks for the client
    @do
    def try_ask_client():
        return (yield Ask("openai_client"))
    
    # Use Catch to handle KeyError
    client = yield Catch(
        try_ask_client(),
        lambda e: None if isinstance(e, KeyError) else None
    )
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
    
    api_key = yield Catch(
        try_ask_api_key(),
        lambda e: None if isinstance(e, KeyError) else None
    )
    
    # If not found, try State
    if not api_key:
        api_key = yield Get("openai_api_key")
    
    # Create client (api_key might be None, which is ok - OpenAI client will use its own defaults)
    client = OpenAIClient(api_key=api_key)
    
    # Store in state for reuse
    yield Put("openai_client", client)
    
    return client


def extract_token_usage(response: Union[ChatCompletion, CreateEmbeddingResponse]) -> Optional[TokenUsage]:
    """Extract token usage from OpenAI response."""
    if hasattr(response, "usage") and response.usage:
        usage = response.usage
        return TokenUsage(
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            total_tokens=getattr(usage, "total_tokens", 0),
        )
    return None


def extract_request_id(response: Any) -> Optional[str]:
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
    request_data: Dict[str, Any],
    response: Any,
    start_time: float,
    error: Optional[Exception] = None,
) -> EffectGenerator[APICallMetadata]:
    """Track an API call with Graph and Log effects."""
    end_time = time.time()
    latency_ms = (end_time - start_time) * 1000
    
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
        stream=request_data.get("stream", False),
    )
    
    # Log the API call
    if error:
        yield Log(f"OpenAI API error: operation={operation}, model={model}, error={error}, latency={latency_ms:.2f}ms")
    else:
        log_msg = f"OpenAI API call: operation={operation}, model={model}, latency={latency_ms:.2f}ms"
        if token_usage:
            log_msg += f", tokens={token_usage.total_tokens}"
        if cost_info:
            log_msg += f", cost=${cost_info.total_cost:.6f}"
        yield Log(log_msg)
    
    # Create Graph step with full metadata
    graph_metadata = metadata.to_graph_metadata()
    
    # Include request summary in graph (not full request to avoid bloat)
    request_summary = {
        "operation": operation,
        "model": model,
    }
    if operation == "chat.completion":
        request_summary["messages_count"] = len(request_data.get("messages", []))
        request_summary["temperature"] = request_data.get("temperature")
        request_summary["max_tokens"] = request_data.get("max_tokens")
    elif operation == "embedding":
        input_data = request_data.get("input", "")
        if isinstance(input_data, list):
            request_summary["input_count"] = len(input_data)
        else:
            request_summary["input_length"] = len(input_data)
    
    # Add request as graph step
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
    
    # Track cumulative cost in state
    if cost_info:
        current_total = yield Get("total_openai_cost")
        new_total = (current_total or 0.0) + cost_info.total_cost
        yield Put("total_openai_cost", new_total)
        
        # Also track per-model costs
        model_cost_key = f"openai_cost_{model}"
        current_model_cost = yield Get(model_cost_key)
        new_model_cost = (current_model_cost or 0.0) + cost_info.total_cost
        yield Put(model_cost_key, new_model_cost)
    
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
    yield Log("Reset OpenAI cost tracking")
    return None