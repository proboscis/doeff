"""Structured LLM implementation using doeff effects for OpenAI API.

This module provides structured output capabilities with full observability
through Effects, supporting Pydantic models, GPT-5 thinking modes, and
comprehensive cost/token tracking.
"""

import asyncio
import base64
import io
import json
import time
from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from doeff import (
    do,
    EffectGenerator,
    Ask,
    Get,
    Put,
    Log,
    Step,
    IO,
    Fail,
    Catch,
    Retry,
)

from doeff_openai.client import (
    get_openai_client,
    track_api_call,
    APICallMetadata,
)

if TYPE_CHECKING:
    import PIL.Image


def is_gpt5_model(model: str) -> bool:
    """Check if the model is a GPT-5 variant."""
    return "gpt-5" in model.lower() or "gpt5" in model.lower()


def requires_max_completion_tokens(model: str) -> bool:
    """Check if model requires max_completion_tokens instead of max_tokens."""
    model_lower = model.lower()
    # GPT-5 and o1/o3/o4 models require max_completion_tokens
    return (
        "gpt-5" in model_lower
        or "gpt5" in model_lower
        or model_lower.startswith("o1")
        or model_lower.startswith("o3")
        or model_lower.startswith("o4")
    )


def convert_pil_to_base64(image: "PIL.Image.Image") -> str:
    """Convert PIL image to base64 string for OpenAI API."""
    # Convert PIL image to bytes
    buffered = io.BytesIO()
    image.save(buffered, format="PNG")
    img_bytes = buffered.getvalue()
    
    # Encode to base64
    img_base64 = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:image/png;base64,{img_base64}"


def ensure_strict_schema(schema: dict) -> dict:
    """
    Recursively ensure all objects in schema have additionalProperties: false.
    This is required for OpenAI's strict mode.
    """
    if not isinstance(schema, dict):
        return schema
    
    # Create a copy to avoid mutation
    result = dict(schema)
    
    # Set additionalProperties: false for this object
    if "type" in result and result["type"] == "object":
        result["additionalProperties"] = False
    
    # Process nested schemas in properties
    if "properties" in result:
        new_properties = {}
        for prop_name, prop_schema in result["properties"].items():
            new_properties[prop_name] = ensure_strict_schema(prop_schema)
        result["properties"] = new_properties
    
    # Process items in arrays
    if "items" in result:
        result["items"] = ensure_strict_schema(result["items"])
    
    # Process nested definitions/defs
    if "definitions" in result:
        new_definitions = {}
        for def_name, def_schema in result["definitions"].items():
            new_definitions[def_name] = ensure_strict_schema(def_schema)
        result["definitions"] = new_definitions
    
    if "$defs" in result:
        new_defs = {}
        for def_name, def_schema in result["$defs"].items():
            new_defs[def_name] = ensure_strict_schema(def_schema)
        result["$defs"] = new_defs
    
    return result


@do
def build_messages(
    text: str,
    images: Optional[List["PIL.Image.Image"]] = None,
    detail: str = "auto",
) -> EffectGenerator[List[Dict[str, Any]]]:
    """
    Build the messages array for OpenAI API call.
    
    Args:
        text: The prompt text
        images: Optional list of PIL images to include
        detail: Image detail level ("auto", "low", "high")
    
    Returns:
        List of message dictionaries for the API call
    """
    yield Log(f"Building messages with {len(images) if images else 0} images")
    
    messages = []
    
    # Build content for the user message
    content = [{"type": "text", "text": text}]
    
    # Add images if provided
    if images:
        for i, img in enumerate(images):
            yield Log(f"Converting image {i+1}/{len(images)} to base64")
            img_base64 = convert_pil_to_base64(img)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": img_base64,
                        "detail": detail,
                    },
                }
            )
    
    messages.append(
        {
            "role": "user",
            "content": content if images else text,  # Use simple text if no images
        }
    )
    
    return messages


@do
def build_api_parameters(
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float,
    max_tokens: int,
    reasoning_effort: Optional[str],
    verbosity: Optional[str],
    service_tier: Optional[str],
    response_format: Optional[type[BaseModel]],
    **kwargs,
) -> EffectGenerator[Dict[str, Any]]:
    """
    Build API parameters for OpenAI API call with model-specific handling.
    
    Args:
        model: OpenAI model name
        messages: List of message dictionaries
        temperature: Temperature for sampling
        max_tokens: Maximum tokens for completion
        reasoning_effort: GPT-5 thinking mode control
        verbosity: GPT-5 output detail control
        service_tier: Service tier for request prioritization
        response_format: Optional Pydantic BaseModel for structured output
        **kwargs: Additional API parameters
    
    Returns:
        Dictionary of API parameters ready for OpenAI API call
    """
    yield Log(f"Building API parameters for model={model}")
    
    # Build the API call parameters
    api_params = {
        "model": model,
        "messages": messages,
        **kwargs,  # Pass through any additional parameters
    }
    
    # Model-specific handling
    model_lower = model.lower()
    
    # Handle temperature
    if (
        is_gpt5_model(model)
        or model_lower.startswith("o1")
        or model_lower.startswith("o3")
        or model_lower.startswith("o4")
    ):
        # These models only support default temperature (1.0)
        if temperature != 1.0:
            yield Log(f"{model} only supports temperature=1.0, ignoring temperature={temperature}")
    else:
        # Other models support custom temperature
        api_params["temperature"] = temperature
    
    # Handle token parameter based on model
    if requires_max_completion_tokens(model):
        # GPT-5, o1, o3, o4 models require max_completion_tokens
        # Ensure sufficient tokens for reasoning + output
        token_value = max(max_tokens, 500)  # Minimum 500 for these models
        api_params["max_completion_tokens"] = token_value
        yield Log(f"Using max_completion_tokens={token_value} for model {model}")
        
        # Add GPT-5 thinking mode parameters
        if reasoning_effort:
            valid_efforts = ["minimal", "low", "medium", "high"]
            if reasoning_effort not in valid_efforts:
                yield Log(f"Invalid reasoning_effort '{reasoning_effort}'. Valid options: {valid_efforts}")
            else:
                api_params["reasoning_effort"] = reasoning_effort
                effort_descriptions = {
                    "minimal": "Minimal reasoning for fast response.",
                    "low": "Light reasoning for moderate complexity.",
                    "medium": "Balanced reasoning for most cases.",
                    "high": "Deep reasoning for complex problems.",
                }
                yield Log(f"Using reasoning_effort='{reasoning_effort}': {effort_descriptions[reasoning_effort]}")
        
        if verbosity:
            valid_verbosity = ["low", "medium", "high"]
            if verbosity not in valid_verbosity:
                yield Log(f"Invalid verbosity '{verbosity}'. Valid options: {valid_verbosity}")
            else:
                api_params["verbosity"] = verbosity
                yield Log(f"Using verbosity='{verbosity}' for output detail control")
    else:
        # Other models use max_tokens
        api_params["max_tokens"] = max_tokens
        yield Log(f"Using max_tokens={max_tokens} for model {model}")
        
        # Warn if GPT-5 specific parameters are used with non-GPT-5 models
        if reasoning_effort:
            yield Log(f"reasoning_effort parameter is only supported for GPT-5 models, ignoring for {model}")
        if verbosity:
            yield Log(f"verbosity parameter is only supported for GPT-5 models, ignoring for {model}")
    
    # Add service_tier if provided
    if service_tier is not None:
        valid_service_tiers = ["auto", "default", "flex", "priority"]
        if service_tier not in valid_service_tiers:
            yield Log(f"Invalid service_tier '{service_tier}'. Valid options: {valid_service_tiers}")
        else:
            api_params["service_tier"] = service_tier
            yield Log(f"Using service_tier='{service_tier}' for request prioritization")
    
    # Handle structured output if requested
    if response_format is not None and issubclass(response_format, BaseModel):
        yield Log(f"Using structured output with {response_format.__name__}")
        
        # Get the JSON schema
        schema = response_format.model_json_schema()
        
        # Ensure strict mode compliance
        strict_schema = ensure_strict_schema(schema)
        
        # Set up the response format for OpenAI
        api_params["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": response_format.__name__,
                "schema": strict_schema,
                "strict": True,
            },
        }
    
    return api_params


@do
def process_structured_response(
    response: Any,
    response_format: type[BaseModel],
) -> EffectGenerator[Any]:
    """
    Process structured response from OpenAI API.
    
    Args:
        response: OpenAI API response object
        response_format: Pydantic BaseModel for structured output
    
    Returns:
        Parsed response according to response_format
    
    Raises:
        JSONDecodeError: If response content is not valid JSON
        ValidationError: If response doesn't match the expected format
    """
    # Extract the content
    content = response.choices[0].message.content
    
    # Parse the JSON response
    from doeff import Catch, do, Fail
    
    @do
    def parse_json():
        yield Log(f"Parsing JSON response for {response_format.__name__}")
        parsed_json = json.loads(content)
        result = response_format(**parsed_json)
        yield Log(f"Successfully parsed response as {response_format.__name__}")
        return result
    
    @do
    def handle_parse_error(e):
        yield Log(f"Failed to parse structured response: {e}")
        yield Log(f"Raw content: {content[:500]}...")
        yield Fail(e)
    
    result = yield Catch(parse_json(), handle_parse_error)
    return result


@do
def process_unstructured_response(response: Any) -> EffectGenerator[str]:
    """
    Process unstructured text response from OpenAI API.
    
    Args:
        response: OpenAI API response object
    
    Returns:
        Raw text content from the response
    """
    # Return the raw text content
    content = response.choices[0].message.content
    
    if len(content) > 100:
        yield Log(f"Received response: {content[:100]}...")
    else:
        yield Log(f"Received response: {content}")
    
    return content


@do
def structured_llm__openai(
    text: str,
    model: str = "gpt-4o",
    images: Optional[List["PIL.Image.Image"]] = None,
    response_format: Optional[type[BaseModel]] = None,
    max_tokens: int = 8192,
    temperature: float = 0.7,
    reasoning_effort: Optional[str] = None,
    verbosity: Optional[str] = None,
    service_tier: Optional[str] = None,
    detail: str = "auto",
    max_retries: int = 3,
    **kwargs,
) -> EffectGenerator[Any]:
    """
    Structured LLM implementation using doeff effects for OpenAI API.
    
    This function provides structured output capabilities with full observability
    through Effects, supporting Pydantic models, GPT-5 thinking modes, and
    comprehensive cost/token tracking.
    
    Args:
        text: The prompt text
        model: OpenAI model name (e.g., "gpt-4o", "gpt-5-nano", "gpt-5")
        images: Optional list of PIL images to include
        response_format: Optional Pydantic BaseModel for structured output
        max_tokens: Maximum tokens for completion (transformed to max_completion_tokens for GPT-5)
        temperature: Temperature for sampling (GPT-5 only supports 1.0)
        reasoning_effort: GPT-5 thinking mode control. Options:
            - "minimal": Few/no reasoning tokens, fastest response for simple tasks
            - "low": Light reasoning for moderately complex tasks
            - "medium": Balanced reasoning for most use cases (default for complex prompts)
            - "high": Deep reasoning for complex problems requiring extensive thinking
            Note: Higher effort = more reasoning tokens = higher cost but better quality
        verbosity: GPT-5 output detail control. Options:
            - "low": Concise responses
            - "medium": Balanced detail (default)
            - "high": Detailed, comprehensive responses
        service_tier: Service tier for request prioritization. Options:
            - "auto": Automatically choose the best tier (default)
            - "default": Standard processing tier
            - "flex": Flexible processing with potentially lower cost
            - "priority": High-priority processing for faster response
        detail: Image detail level for vision models ("auto", "low", "high")
        max_retries: Maximum number of retry attempts for API calls
        **kwargs: Additional OpenAI API parameters
    
    GPT-5 Thinking Mode Usage:
        For simple tasks (extraction, formatting, classification):
            reasoning_effort="minimal"  # Fastest, lowest cost
        
        For complex reasoning (math, analysis, planning):
            reasoning_effort="high"  # Best quality, shows thinking process
        
        The model automatically uses "reasoning tokens" (invisible tokens that count
        toward billing) to think through problems before generating the response.
        You can see reasoning token usage in the response.usage.completion_tokens_details.
    
    Returns:
        Parsed response according to response_format, or raw text if no format specified
    
    Effects Used:
        - Log: Tracks all operations and decisions
        - Step: Creates graph nodes for observability
        - Get/Put: Manages state for cost tracking
        - IO: Makes API calls
        - Retry: Handles transient failures
        - Catch: Handles parsing errors
    """
    yield Log(f"structured_llm__openai called with model={model}, response_format={response_format}")
    
    # Track in graph
    yield Step(
        value={"operation": "structured_llm", "model": model},
        meta={"type": "llm_call", "has_images": images is not None, "structured": response_format is not None}
    )
    
    # Phase 1: Build messages
    messages = yield build_messages(text, images, detail)
    
    # Phase 2: Build API parameters
    api_params = yield build_api_parameters(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        service_tier=service_tier,
        response_format=response_format,
        **kwargs,
    )
    
    # Phase 3: Get OpenAI client
    client = yield get_openai_client()
    
    # Phase 4: Make API call with retry
    start_time = time.time()
    
    @do
    def make_api_call():
        yield Log(f"Making OpenAI API call with model={model}")
        # Use sync client for simplicity (async client handling could be added later)
        response = yield IO(lambda: client.sync_client.chat.completions.create(**api_params))
        return response
    
    # Use Retry effect for transient failures
    response = yield Retry(make_api_call(), max_attempts=max_retries, delay_ms=1000)
    
    # Phase 5: Track the API call
    metadata = yield track_api_call(
        operation="structured_llm",
        model=model,
        request_data=api_params,
        response=response,
        start_time=start_time,
        error=None,
    )
    
    # Log token usage details for GPT-5 models
    if is_gpt5_model(model) and hasattr(response.usage, "completion_tokens_details"):
        details = response.usage.completion_tokens_details
        if hasattr(details, "reasoning_tokens"):
            yield Log(f"GPT-5 reasoning tokens used: {details.reasoning_tokens}")
            yield Log(f"GPT-5 output tokens: {details.output_tokens if hasattr(details, 'output_tokens') else 'N/A'}")
    
    # Phase 6: Process response based on format
    if response_format is not None and issubclass(response_format, BaseModel):
        result = yield process_structured_response(response, response_format)
    else:
        result = yield process_unstructured_response(response)
    
    # Track result in graph
    yield Step(
        value={"result_type": type(result).__name__ if response_format else "str"},
        meta={"tokens_used": response.usage.total_tokens if response.usage else 0}
    )
    
    return result


# Convenience functions for specific models
@do
def gpt4o_structured(
    text: str,
    images: Optional[List["PIL.Image.Image"]] = None,
    response_format: Optional[type[BaseModel]] = None,
    **kwargs,
) -> EffectGenerator[Any]:
    """Convenience function for GPT-4o with structured output."""
    return (yield structured_llm__openai(
        text=text,
        model="gpt-4o",
        images=images,
        response_format=response_format,
        **kwargs,
    ))


@do
def gpt5_nano_structured(
    text: str,
    images: Optional[List["PIL.Image.Image"]] = None,
    response_format: Optional[type[BaseModel]] = None,
    reasoning_effort: str = "minimal",
    **kwargs,
) -> EffectGenerator[Any]:
    """
    Convenience function for GPT-5-nano with structured output.
    
    Defaults to minimal reasoning effort for fast responses.
    """
    return (yield structured_llm__openai(
        text=text,
        model="gpt-5-nano",
        images=images,
        response_format=response_format,
        reasoning_effort=reasoning_effort,
        **kwargs,
    ))


@do
def gpt5_structured(
    text: str,
    images: Optional[List["PIL.Image.Image"]] = None,
    response_format: Optional[type[BaseModel]] = None,
    reasoning_effort: str = "medium",
    **kwargs,
) -> EffectGenerator[Any]:
    """
    Convenience function for GPT-5 with structured output.
    
    Defaults to medium reasoning effort for balanced performance.
    """
    return (yield structured_llm__openai(
        text=text,
        model="gpt-5",
        images=images,
        response_format=response_format,
        reasoning_effort=reasoning_effort,
        **kwargs,
    ))


__all__ = [
    "structured_llm__openai",
    "gpt4o_structured",
    "gpt5_nano_structured",
    "gpt5_structured",
    "is_gpt5_model",
    "requires_max_completion_tokens",
]