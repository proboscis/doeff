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
from typing import Any

import PIL.Image
from pydantic import BaseModel

from doeff import (
    Await,
    EffectGenerator,
    Tell,
    Try,
    do,
)
from doeff_openai.client import (
    get_openai_client,
    track_api_call,
)


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


def _collect_message_content_parts(content: Any) -> tuple[Any | None, list[str]]:
    """Extract JSON payload (if any) and text fragments from a message content."""
    if content is None:
        return None, []

    if isinstance(content, str):
        return None, [content]

    json_payload: Any | None = None
    text_parts: list[str] = []

    if isinstance(content, list):
        for part in content:
            part_type = getattr(part, "type", None)

            # Extract possible JSON payload
            part_json = None
            if isinstance(part, dict):
                part_json = part.get("json")
            else:
                part_json = getattr(part, "json", None)

            if part_json is not None and json_payload is None:
                json_payload = part_json

            # Extract textual fragments
            part_text = None
            if isinstance(part, dict):
                part_text = (
                    part.get("text")
                    or part.get("input_text")
                    or part.get("output_text")
                    or part.get("content")
                )
            else:
                part_text = getattr(part, "text", None)
                if part_text is None:
                    part_text = getattr(part, "content", None)

            if part_text is not None:
                if isinstance(part_text, list):
                    text_parts.extend(str(item) for item in part_text if item is not None)
                else:
                    text_parts.append(str(part_text))

            # Some payloads encode JSON inside the text field
            if part_json is None and isinstance(part_text, (dict, list)) and json_payload is None:
                json_payload = part_text

            # Fallback for known json-specific types
            if json_payload is None and part_type in {"output_json", "json"}:
                candidate = None
                if isinstance(part, dict):
                    candidate = part.get("content") or part.get("data")
                else:
                    candidate = getattr(part, "content", None)
                if candidate is not None:
                    if isinstance(candidate, (dict, list)):
                        json_payload = candidate
                    else:
                        text_parts.append(str(candidate))
    else:
        text_parts.append(str(content))

    return json_payload, text_parts


def _stringify_for_log(content: Any, limit: int = 500) -> str:
    """Prepare a compact string representation for logging purposes."""
    try:
        if isinstance(content, str):
            text = content
        elif isinstance(content, (dict, list)):
            text = json.dumps(content)
        else:
            text = str(content)
    except Exception:  # pragma: no cover - extremely defensive
        text = str(content)

    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


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

        # Enforce that required contains every property name for strict mode
        property_names = list(new_properties.keys())
        if property_names:
            existing_required = list(result.get("required", []))
            seen = set(existing_required)
            for name in property_names:
                if name not in seen:
                    existing_required.append(name)
                    seen.add(name)
            result["required"] = existing_required

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
    images: list["PIL.Image.Image"] | None = None,
    detail: str = "auto",
) -> EffectGenerator[list[dict[str, Any]]]:
    """
    Build the messages array for OpenAI API call.

    Args:
        text: The prompt text
        images: Optional list of PIL images to include
        detail: Image detail level ("auto", "low", "high")

    Returns:
        List of message dictionaries for the API call
    """
    yield Tell(f"Building messages with {len(images) if images else 0} images")

    messages = []

    # Build content for the user message
    content = [{"type": "text", "text": text}]

    # Add images if provided
    if images:
        for i, img in enumerate(images):
            yield Tell(f"Converting image {i + 1}/{len(images)} to base64")
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
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    reasoning_effort: str | None,
    verbosity: str | None,
    service_tier: str | None,
    response_format: type[BaseModel] | None,
    **kwargs,
) -> EffectGenerator[dict[str, Any]]:
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
    yield Tell(f"Building API parameters for model={model}")

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
            yield Tell(f"{model} only supports temperature=1.0, ignoring temperature={temperature}")
    else:
        # Other models support custom temperature
        api_params["temperature"] = temperature

    # Handle token parameter based on model
    if requires_max_completion_tokens(model):
        # GPT-5, o1, o3, o4 models require max_completion_tokens
        # Ensure sufficient tokens for reasoning + output
        token_value = max(max_tokens, 500)  # Minimum 500 for these models
        api_params["max_completion_tokens"] = token_value
        yield Tell(f"Using max_completion_tokens={token_value} for model {model}")

        # Add GPT-5 thinking mode parameters
        if reasoning_effort:
            valid_efforts = ["minimal", "low", "medium", "high"]
            if reasoning_effort not in valid_efforts:
                yield Tell(
                    f"Invalid reasoning_effort '{reasoning_effort}'. Valid options: {valid_efforts}"
                )
            else:
                api_params["reasoning_effort"] = reasoning_effort
                effort_descriptions = {
                    "minimal": "Minimal reasoning for fast response.",
                    "low": "Light reasoning for moderate complexity.",
                    "medium": "Balanced reasoning for most cases.",
                    "high": "Deep reasoning for complex problems.",
                }
                yield Tell(
                    f"Using reasoning_effort='{reasoning_effort}': {effort_descriptions[reasoning_effort]}"
                )

        if verbosity:
            valid_verbosity = ["low", "medium", "high"]
            if verbosity not in valid_verbosity:
                yield Tell(f"Invalid verbosity '{verbosity}'. Valid options: {valid_verbosity}")
            else:
                api_params["verbosity"] = verbosity
                yield Tell(f"Using verbosity='{verbosity}' for output detail control")
    else:
        # Other models use max_tokens
        api_params["max_tokens"] = max_tokens
        yield Tell(f"Using max_tokens={max_tokens} for model {model}")

        # Warn if GPT-5 specific parameters are used with non-GPT-5 models
        if reasoning_effort:
            yield Tell(
                f"reasoning_effort parameter is only supported for GPT-5 models, ignoring for {model}"
            )
        if verbosity:
            yield Tell(
                f"verbosity parameter is only supported for GPT-5 models, ignoring for {model}"
            )

    # Add service_tier if provided
    if service_tier is not None:
        valid_service_tiers = ["auto", "default", "flex", "priority"]
        if service_tier not in valid_service_tiers:
            yield Tell(
                f"Invalid service_tier '{service_tier}'. Valid options: {valid_service_tiers}"
            )
        else:
            api_params["service_tier"] = service_tier
            yield Tell(f"Using service_tier='{service_tier}' for request prioritization")

    # Handle structured output if requested
    if response_format is not None and issubclass(response_format, BaseModel):
        yield Tell(f"Using structured output with {response_format.__name__}")

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
    message = response.choices[0].message
    content = getattr(message, "content", None)

    json_payload, text_parts = _collect_message_content_parts(content)

    # Prepare a source for parsing and logging
    parse_source: Any
    if json_payload is not None:
        parse_source = json_payload
    elif text_parts:
        parse_source = "\n".join(part for part in text_parts if part)
    else:
        parse_source = ""

    raw_content_for_log = _stringify_for_log(parse_source)

    # Parse the JSON response
    @do
    def parse_json():
        yield Tell(f"Parsing JSON response for {response_format.__name__}")

        if isinstance(parse_source, str):
            parsed_json = json.loads(parse_source)
        else:
            parsed_json = parse_source

        if hasattr(response_format, "model_validate"):
            result_model = response_format.model_validate(parsed_json)  # type: ignore[attr-defined]
        else:
            result_model = response_format(**parsed_json)

        yield Tell(f"Successfully parsed response as {response_format.__name__}")
        return result_model

    # Execute with Try to handle errors
    safe_result = yield Try(parse_json())
    if safe_result.is_err():
        e = safe_result.error
        yield Tell(f"Failed to parse structured response: {e}")
        yield Tell(f"Raw content: {raw_content_for_log}")
        raise e
    return safe_result.value


@do
def process_unstructured_response(response: Any) -> EffectGenerator[str]:
    """
    Process unstructured text response from OpenAI API.

    Args:
        response: OpenAI API response object

    Returns:
        Raw text content from the response
    """
    message = response.choices[0].message
    content = getattr(message, "content", None)
    _, text_parts = _collect_message_content_parts(content)

    text = " ".join(part.strip() for part in text_parts if part).strip()

    log_preview = _stringify_for_log(text, limit=100)
    yield Tell(f"Received response: {log_preview}")

    return text


@do
def structured_llm__openai(
    text: str,
    model: str = "gpt-4o",
    images: list["PIL.Image.Image"] | None = None,
    response_format: type[BaseModel] | None = None,
    max_tokens: int = 8192,
    temperature: float = 0.7,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    service_tier: str | None = None,
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
        - Await: Makes async API calls with the async OpenAI client
        - Retry: Handles transient failures
        - Try: Handles parsing errors
    """
    yield Tell(
        f"structured_llm__openai called with model={model}, response_format={response_format}"
    )

    yield Tell(
        f"Structured LLM tracking: operation=structured_llm, model={model}, "
        f"has_images={images is not None}, structured={response_format is not None}"
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
    @do
    def make_api_call():
        # Track start time for this specific attempt
        attempt_start_time = time.time()
        yield Tell(f"Making OpenAI API call with model={model}")

        # Use Try to handle errors and track them
        @do
        def api_call_with_tracking():
            # Make the actual API call
            response = yield Await(client.async_client.chat.completions.create(**api_params))

            # Track successful API call
            metadata = yield track_api_call(
                operation="structured_llm",
                model=model,
                request_payload=api_params,
                response=response,
                start_time=attempt_start_time,
                error=None,
            )
            return response

        # Use Try to track both success and failure
        safe_result = yield Try(api_call_with_tracking())
        if safe_result.is_err():
            error = safe_result.error
            # Track failed API call attempt (tracking will log the error)
            metadata = yield track_api_call(
                operation="structured_llm",
                model=model,
                request_payload=api_params,
                response=None,
                start_time=attempt_start_time,
                error=error,
            )
            # Re-raise to trigger retry
            raise error
        return safe_result.value

    # Retry logic for transient failures
    delay_seconds = 1.0
    last_error = None
    for attempt in range(max_retries):
        safe_result = yield Try(make_api_call())
        if safe_result.is_ok():
            response = safe_result.value
            break
        last_error = safe_result.error
        if attempt < max_retries - 1:
            yield Tell(
                f"OpenAI API call failed (attempt {attempt + 1}/{max_retries}), retrying in {delay_seconds}s..."
            )
            yield Await(asyncio.sleep(delay_seconds))
    else:
        assert last_error is not None, "Should have an error if all retries failed"
        raise last_error

    # Log token usage details for GPT-5 models
    if is_gpt5_model(model) and hasattr(response.usage, "completion_tokens_details"):
        details = response.usage.completion_tokens_details
        if hasattr(details, "reasoning_tokens"):
            yield Tell(f"GPT-5 reasoning tokens used: {details.reasoning_tokens}")
            yield Tell(
                f"GPT-5 output tokens: {details.output_tokens if hasattr(details, 'output_tokens') else 'N/A'}"
            )

    # Phase 6: Process response based on format
    if response_format is not None and issubclass(response_format, BaseModel):
        result = yield process_structured_response(response, response_format)
    else:
        result = yield process_unstructured_response(response)

    yield Tell(
        f"Structured LLM result tracking: result_type={type(result).__name__ if response_format else 'str'}, "
        f"tokens_used={response.usage.total_tokens if response.usage else 0}"
    )

    return result


# Convenience functions for specific models
@do
def gpt4o_structured(
    text: str,
    images: list["PIL.Image.Image"] | None = None,
    response_format: type[BaseModel] | None = None,
    **kwargs,
) -> EffectGenerator[Any]:
    """Convenience function for GPT-4o with structured output."""
    return (
        yield structured_llm__openai(
            text=text,
            model="gpt-4o",
            images=images,
            response_format=response_format,
            **kwargs,
        )
    )


@do
def gpt5_nano_structured(
    text: str,
    images: list["PIL.Image.Image"] | None = None,
    response_format: type[BaseModel] | None = None,
    reasoning_effort: str = "minimal",
    **kwargs,
) -> EffectGenerator[Any]:
    """
    Convenience function for GPT-5-nano with structured output.

    Defaults to minimal reasoning effort for fast responses.
    """
    return (
        yield structured_llm__openai(
            text=text,
            model="gpt-5-nano",
            images=images,
            response_format=response_format,
            reasoning_effort=reasoning_effort,
            **kwargs,
        )
    )


@do
def gpt5_structured(
    text: str,
    images: list["PIL.Image.Image"] | None = None,
    response_format: type[BaseModel] | None = None,
    reasoning_effort: str = "medium",
    **kwargs,
) -> EffectGenerator[Any]:
    """
    Convenience function for GPT-5 with structured output.

    Defaults to medium reasoning effort for balanced performance.
    """
    return (
        yield structured_llm__openai(
            text=text,
            model="gpt-5",
            images=images,
            response_format=response_format,
            reasoning_effort=reasoning_effort,
            **kwargs,
        )
    )


__all__ = [
    "gpt4o_structured",
    "gpt5_nano_structured",
    "gpt5_structured",
    "is_gpt5_model",
    "requires_max_completion_tokens",
    "structured_llm__openai",
]
