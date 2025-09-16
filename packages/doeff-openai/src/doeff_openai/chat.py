"""Chat completion operations with comprehensive observability."""

import time
from typing import Any, Dict, List, Optional, Union, AsyncIterator
from datetime import datetime

from openai.types.chat import ChatCompletion, ChatCompletionChunk
from openai.types.chat.chat_completion_message_param import ChatCompletionMessageParam

from doeff import (
    do,
    Effect,
    EffectGenerator,
    Log,
    Step,
    IO,
    Await,
)

from doeff_openai.client import get_openai_client, track_api_call
from doeff_openai.types import (
    CompletionRequest,
    StreamChunk,
    TokenUsage,
)
from doeff_openai.costs import (
    count_message_tokens,
    count_tokens,
    calculate_cost,
)


@do
def chat_completion(
    messages: List[Union[Dict[str, Any], ChatCompletionMessageParam]],
    model: str = "gpt-3.5-turbo",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    stop: Optional[Union[str, List[str]]] = None,
    stream: bool = False,
    user: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
    response_format: Optional[Dict[str, Any]] = None,
    seed: Optional[int] = None,
    **kwargs: Any,
) -> EffectGenerator[Union[ChatCompletion, AsyncIterator[ChatCompletionChunk]]]:
    """
    Create a chat completion with full observability.
    
    Tracks:
    - Request/response in Graph with metadata
    - Token usage and costs
    - Latency
    - Errors
    """
    # Log the request
    yield Log(f"OpenAI chat request: model={model}, messages={len(messages)}, stream={stream}")
    
    # Count input tokens
    input_tokens = count_message_tokens(messages, model)
    yield Log(f"Estimated input tokens: {input_tokens}")
    
    # Build request data
    request_data = {
        "messages": messages,
        "model": model,
        "stream": stream,
    }
    
    # Add optional parameters
    if temperature is not None:
        request_data["temperature"] = temperature
    if max_tokens is not None:
        request_data["max_tokens"] = max_tokens
    if top_p is not None:
        request_data["top_p"] = top_p
    if frequency_penalty is not None:
        request_data["frequency_penalty"] = frequency_penalty
    if presence_penalty is not None:
        request_data["presence_penalty"] = presence_penalty
    if stop is not None:
        request_data["stop"] = stop
    if user is not None:
        request_data["user"] = user
    if tools is not None:
        request_data["tools"] = tools
    if tool_choice is not None:
        request_data["tool_choice"] = tool_choice
    if response_format is not None:
        request_data["response_format"] = response_format
    if seed is not None:
        request_data["seed"] = seed
    
    # Add any additional kwargs
    request_data.update(kwargs)
    
    # Get OpenAI client
    client = yield get_openai_client()
    
    # Track start time
    start_time = time.time()
    
    from doeff import Catch, do, Fail
    
    # Define the main operation as a sub-program
    @do
    def main_operation():
        if stream:
            # For streaming, we need to handle differently
            # Create async generator wrapper
            async def create_stream():
                stream_response = await client.async_client.chat.completions.create(**request_data)
                return stream_response
            
            # Use Await effect for async operation
            stream_response = yield Await(create_stream())
            
            # Log streaming start
            yield Log(f"Started streaming chat completion")
            
            # Track the streaming request (no immediate response data)
            metadata = yield track_api_call(
                operation="chat.completion",
                model=model,
                request_data=request_data,
                response=None,  # No immediate response for streaming
                start_time=start_time,
                error=None,
            )
            
            return stream_response
        else:
            # Non-streaming completion
            # Use IO effect for sync API call
            response = yield IO(lambda: client.sync_client.chat.completions.create(**request_data))
            
            # Track the API call with full metadata
            metadata = yield track_api_call(
                operation="chat.completion",
                model=model,
                request_data=request_data,
                response=response,
                start_time=start_time,
                error=None,
            )
            
            # Log completion details
            if response.choices:
                finish_reason = response.choices[0].finish_reason
                content = response.choices[0].message.content
                yield Log(f"Chat completion finished: reason={finish_reason}, content_length={len(content) if content else 0}")
            
            return response
    
    # Use Catch to handle errors
    @do
    def error_handler(e):
        # Track error
        metadata = yield track_api_call(
            operation="chat.completion",
            model=model,
            request_data=request_data,
            response=None,
            start_time=start_time,
            error=e,
        )
        yield Log(f"Chat completion failed: {e}")
        # Re-raise the error
        yield Fail(e)
    
    # Execute with error handling
    result = yield Catch(main_operation(), error_handler)
    return result


@do
def chat_completion_async(
    messages: List[Union[Dict[str, Any], ChatCompletionMessageParam]],
    model: str = "gpt-3.5-turbo",
    **kwargs: Any,
) -> EffectGenerator[ChatCompletion]:
    """
    Create an async chat completion with full observability.
    
    This version uses the async client for better performance in async contexts.
    """
    # Log the request
    yield Log(f"OpenAI async chat request: model={model}, messages={len(messages)}")
    
    # Build request data
    request_data = {
        "messages": messages,
        "model": model,
        **kwargs,
    }
    
    # Get OpenAI client
    client = yield get_openai_client()
    
    # Track start time
    start_time = time.time()
    
    from doeff import Catch, do, Fail
    
    # Define the main operation as a sub-program
    @do
    def main_operation():
        # Use Await effect for async API call
        async def create_completion():
            return await client.async_client.chat.completions.create(**request_data)
        
        response = yield Await(create_completion())
        
        # Track the API call with full metadata
        metadata = yield track_api_call(
            operation="chat.completion",
            model=model,
            request_data=request_data,
            response=response,
            start_time=start_time,
            error=None,
        )
        
        return response
    
    # Use Catch to handle errors
    @do
    def error_handler(e):
        # Track error
        metadata = yield track_api_call(
            operation="chat.completion",
            model=model,
            request_data=request_data,
            response=None,
            start_time=start_time,
            error=e,
        )
        yield Fail(e)
    
    # Execute with error handling
    result = yield Catch(main_operation(), error_handler)
    return result


@do
def process_stream_chunks(
    stream: AsyncIterator[ChatCompletionChunk],
    model: str,
) -> EffectGenerator[List[StreamChunk]]:
    """
    Process streaming chunks with observability.
    
    Accumulates chunks and tracks tokens/costs.
    """
    chunks = []
    full_content = ""
    total_chunks = 0
    
    yield Log(f"Processing streaming chunks for model={model}")
    
    # Process chunks
    async def collect_chunks():
        nonlocal full_content, total_chunks
        collected = []
        
        async for chunk in stream:
            total_chunks += 1
            
            # Extract content from chunk
            if chunk.choices:
                choice = chunk.choices[0]
                if choice.delta and choice.delta.content:
                    content = choice.delta.content
                    full_content += content
                    
                    collected.append(StreamChunk(
                        content=content,
                        role=choice.delta.role,
                        finish_reason=choice.finish_reason,
                        index=choice.index,
                        model=chunk.model,
                    ))
        
        return collected
    
    chunks = yield Await(collect_chunks())
    
    # Calculate tokens for accumulated content
    if full_content:
        output_tokens = count_tokens(full_content, model)
        
        # Create token usage
        token_usage = TokenUsage(
            prompt_tokens=0,  # Already counted before streaming
            completion_tokens=output_tokens,
            total_tokens=output_tokens,
        )
        
        # Calculate cost
        cost_info = calculate_cost(model, token_usage)
        
        # Add final graph step with accumulated data
        yield Step(
            {
                "stream_complete": True,
                "total_chunks": total_chunks,
                "content_length": len(full_content),
            },
            {
                "type": "openai_stream_complete",
                "model": model,
                "output_tokens": output_tokens,
                "cost_usd": cost_info.total_cost,
                "chunks": total_chunks,
            }
        )
        
        yield Log(f"Stream complete: chunks={total_chunks}, tokens={output_tokens}, cost=${cost_info.total_cost:.6f}")
    
    return chunks


@do
def simple_chat(
    prompt: str,
    model: str = "gpt-3.5-turbo",
    system_prompt: Optional[str] = None,
    **kwargs: Any,
) -> EffectGenerator[str]:
    """
    Simple chat interface that returns just the response text.
    
    Still tracks everything via Graph and Log effects.
    """
    messages = []
    
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    
    messages.append({"role": "user", "content": prompt})
    
    response = yield chat_completion(messages, model, **kwargs)
    
    if response.choices:
        content = response.choices[0].message.content
        return content or ""
    
    return ""