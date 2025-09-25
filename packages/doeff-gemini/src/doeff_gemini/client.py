"""Gemini client helpers integrated with doeff effects."""

from __future__ import annotations

import base64
import io
import time
from datetime import datetime, timezone
from typing import Any

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

from .costs import calculate_cost
from .types import APICallMetadata, CostInfo, TokenUsage


def _serialize_image_for_logging(image: Any) -> dict[str, Any]:
    """Serialize a PIL image into a loggable dictionary with base64 data."""
    try:
        image_format = (getattr(image, "format", None) or "PNG").upper()
        buffer = io.BytesIO()
        image.save(buffer, format=image_format)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        mime_type = f"image/{image_format.lower()}"
        return {
            "format": image_format,
            "mode": getattr(image, "mode", None),
            "size": list(getattr(image, "size", ())) or None,
            "mime_type": mime_type,
            "data_uri": f"data:{mime_type};base64,{encoded}",
        }
    except Exception as exc:  # pragma: no cover - defensive logging helper
        return {"error": f"failed_to_serialize_image: {exc}"}


def _prepare_request_payload(
    request_payload: Any,
) -> tuple[Any, str | None, list[dict[str, Any]]]:
    """Normalize Gemini request payload for logging/state tracking."""

    if not isinstance(request_payload, dict):
        return request_payload, None, []

    text = request_payload.get("text") if isinstance(request_payload.get("text"), str) else None
    raw_images = request_payload.get("images") or []
    serialized_images: list[dict[str, Any]] = []
    if isinstance(raw_images, list):
        serialized_images = [_serialize_image_for_logging(image) for image in raw_images]

    sanitized_payload = dict(request_payload)
    sanitized_payload["images"] = serialized_images

    return sanitized_payload, text, serialized_images


class GeminiClient:
    """Lazy wrapper around :mod:`google.genai` client creation."""

    def __init__(
        self,
        api_key: str | None = None,
        vertexai: bool | None = None,
        project: str | None = None,
        location: str | None = None,
        credentials: Any | None = None,
        client_options: dict[str, Any] | None = None,
        **extra_client_kwargs: Any,
    ) -> None:
        self.api_key = api_key
        self.vertexai = vertexai
        self.project = project
        self.location = location
        self.credentials = credentials
        self.client_options = client_options or {}
        self._extra_client_kwargs = extra_client_kwargs

        self._genai_module: Any | None = None
        self._client: Any | None = None

    def _load_module(self):
        if self._genai_module is None:
            from google import genai  # Imported lazily for testability

            self._genai_module = genai
        return self._genai_module

    def _build_client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = dict(self._extra_client_kwargs)
        if self.api_key:
            kwargs.setdefault("api_key", self.api_key)
        if self.vertexai:
            kwargs.setdefault("vertexai", True)
            if self.project:
                kwargs.setdefault("project", self.project)
            if self.location:
                kwargs.setdefault("location", self.location)
            if self.credentials is not None:
                kwargs.setdefault("credentials", self.credentials)
        if self.client_options:
            kwargs.setdefault("client_options", self.client_options)
        return kwargs

    @property
    def client(self):
        """Instantiate and cache :class:`google.genai.Client`."""
        if self._client is None:
            genai = self._load_module()
            kwargs = self._build_client_kwargs()
            self._client = genai.Client(**kwargs)
        return self._client

    @property
    def async_client(self):
        """Asynchronous view of the configured client."""
        return self.client.aio


DEFAULT_LOCATION = "global"


@do
def get_gemini_client() -> EffectGenerator[GeminiClient]:
    """Retrieve a configured :class:`GeminiClient` from Reader or State effects."""

    @do
    def ask(name: str):
        return (yield Ask(name))

    def ask_optional(name: str) -> EffectGenerator[Any]:
        return Catch(ask(name), lambda exc: None if isinstance(exc, KeyError) else None)  # type: ignore[return-value]

    client = yield Catch(
        ask("gemini_client"),
        lambda exc: None if isinstance(exc, KeyError) else None,
    )
    if client:
        return client

    client = yield Get("gemini_client")
    if client:
        return client

    api_key = yield ask_optional("gemini_api_key")
    if api_key is None:
        api_key = yield Get("gemini_api_key")

    vertexai = yield ask_optional("gemini_vertexai")
    if vertexai is None:
        vertexai = yield Get("gemini_vertexai")

    project = yield ask_optional("gemini_project")
    if project is None:
        project = yield Get("gemini_project")

    location = yield ask_optional("gemini_location")
    if location is None:
        location = yield Get("gemini_location")

    credentials = yield ask_optional("gemini_credentials")
    if credentials is None:
        credentials = yield Get("gemini_credentials")

    client_options = yield ask_optional("gemini_client_options")
    if client_options is None:
        client_options = yield Get("gemini_client_options")
    if client_options is not None and not isinstance(client_options, dict):
        client_options = None

    extra_kwargs = yield ask_optional("gemini_client_kwargs")
    if extra_kwargs is None:
        extra_kwargs = yield Get("gemini_client_kwargs")
    if not isinstance(extra_kwargs, dict):
        extra_kwargs = {}

    adc_credentials = None
    adc_project = None

    if not api_key:
        try:
            from google.auth import default as google_auth_default
            from google.auth.exceptions import DefaultCredentialsError
        except ModuleNotFoundError as exc:  # pragma: no cover - configuration issue
            yield Log(
                "google-auth is not installed; install google-auth or set GEMINI API key"
            )
            yield Fail(exc)

        try:
            adc_credentials, adc_project = google_auth_default()
        except DefaultCredentialsError as exc:
            yield Log(
                "Failed to load Google Application Default Credentials. "
                "Run 'gcloud auth application-default login' or provide a GEMINI API key."
            )
            yield Fail(exc)

        yield Log("Using Google Application Default Credentials for Gemini client")

        if adc_project is None and project is None:
            yield Log(
                "Google credentials found but project ID missing. Set 'gemini_project' or configure gcloud."
            )
            yield Fail(ValueError("Google project ID could not be determined."))

        if credentials is None:
            credentials = adc_credentials
        if project is None:
            project = adc_project
        if vertexai is None:
            vertexai = True
    else:
        if vertexai is None:
            vertexai = False
        yield Log("Using Gemini API key authentication")

    if location is None:
        location = DEFAULT_LOCATION if vertexai else None

    client_instance = GeminiClient(
        api_key=api_key,
        vertexai=bool(vertexai) if vertexai is not None else None,
        project=project,
        location=location,
        credentials=credentials,
        client_options=client_options,
        **extra_kwargs,
    )
    yield Put("gemini_client", client_instance)
    return client_instance


def _extract_request_id(response: Any) -> str | None:
    """Extract request identifier from a Gemini response if available."""
    if response is None:
        return None
    for attr_name in ("response_id", "id", "server_response"):
        if hasattr(response, attr_name):
            value = getattr(response, attr_name)
            if isinstance(value, dict):
                request_id = value.get("id") or value.get("request_id")
                if request_id:
                    return request_id
            elif isinstance(value, str):
                return value
    return None


def _extract_usage(response: Any) -> TokenUsage | None:
    """Extract textual token usage metadata from a Gemini response."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None

    def _get_attr(*names: str) -> int | None:
        for name in names:
            if hasattr(usage, name):
                value = getattr(usage, name)
                if value is not None:
                    return int(value)
        return None

    input_tokens = _get_attr(
        "text_input_token_count",
        "input_token_count",
        "prompt_token_count",
    )
    output_tokens = _get_attr("text_output_token_count", "output_token_count")
    image_input_tokens = _get_attr("image_input_token_count")
    image_output_tokens = _get_attr("image_output_token_count")
    total_tokens = _get_attr("total_token_count")

    if (
        input_tokens is None
        and output_tokens is None
        and image_input_tokens is None
        and image_output_tokens is None
        and total_tokens is None
    ):
        return None

    derived_total = total_tokens
    if derived_total is None:
        derived_total = sum(
            token
            for token in [input_tokens, output_tokens, image_input_tokens, image_output_tokens]
            if token is not None
        ) or 0

    return TokenUsage(
        input_tokens=input_tokens or 0,
        output_tokens=output_tokens or 0,
        total_tokens=derived_total,
        text_input_tokens=input_tokens,
        text_output_tokens=output_tokens,
        image_input_tokens=image_input_tokens,
        image_output_tokens=image_output_tokens,
    )


@do
def track_api_call(
    operation: str,
    model: str,
    request_summary: dict[str, Any],
    request_payload: Any,
    response: Any,
    start_time: float,
    error: Exception | None = None,
) -> EffectGenerator[APICallMetadata]:
    """Log and persist observability metadata for a Gemini API invocation."""
    end_time = time.time()
    latency_ms = (end_time - start_time) * 1000

    sanitized_payload, prompt_text, prompt_images = _prepare_request_payload(request_payload)

    token_usage = _extract_usage(response) if response and not error else None
    request_id = _extract_request_id(response) if response else None

    cost_info: CostInfo | None = None
    if token_usage:
        usage_for_cost = token_usage.to_cost_usage()
        if usage_for_cost:
            try:
                cost_info = calculate_cost(model, usage_for_cost)
            except ValueError as exc:
                yield Log(f"Gemini cost calculation unavailable: {exc}")

    metadata = APICallMetadata(
        operation=operation,
        model=model,
        timestamp=datetime.now(timezone.utc),
        request_id=request_id,
        latency_ms=latency_ms,
        token_usage=token_usage,
        cost_info=cost_info,
        error=str(error) if error else None,
    )

    if error:
        yield Log(
            f"Gemini API error: operation={operation}, model={model}, latency={latency_ms:.2f}ms, error={error}"
        )
    else:
        log_line = f"Gemini API call: operation={operation}, model={model}, latency={latency_ms:.2f}ms"
        if token_usage:
            log_line += f", tokens={token_usage.total_tokens}"
        if cost_info:
            log_line += f", cost=${cost_info.total_cost:.6f}"
        yield Log(log_line)

    graph_meta = metadata.to_graph_metadata()
    yield Step(
        {"request_payload": sanitized_payload, "timestamp": graph_meta["timestamp"]},
        {**graph_meta, "phase": "request_payload"},
    )
    yield Step(
        {"request": request_summary, "timestamp": graph_meta["timestamp"]},
        {**graph_meta, "phase": "request"},
    )

    if error:
        yield Step(
            {"error": str(error)},
            {**graph_meta, "phase": "error"},
        )
    else:
        yield Step(
            {"response": {"success": True, "model": model}},
            {**graph_meta, "phase": "response"},
        )

    if token_usage:
        yield Step(
            {"usage": token_usage.to_dict()},
            {**graph_meta, "phase": "usage"},
        )

    if cost_info:
        yield Step(
            {"cost": cost_info.to_dict()},
            {**graph_meta, "phase": "cost"},
        )

    api_calls = yield Get("gemini_api_calls")
    if api_calls is None:
        api_calls = []

    api_calls.append(
        {
            "operation": operation,
            "model": model,
            "timestamp": metadata.timestamp.isoformat(),
            "latency_ms": latency_ms,
            "error": metadata.error,
            "tokens": {
                "input": token_usage.input_tokens if token_usage else None,
                "output": token_usage.output_tokens if token_usage else None,
                "total": token_usage.total_tokens if token_usage else None,
            }
            if token_usage
            else None,
            "request_id": request_id,
            "cost": cost_info.total_cost if cost_info else None,
            "cost_breakdown": {
                "text_input": cost_info.text_input_cost if cost_info else None,
                "text_output": cost_info.text_output_cost if cost_info else None,
                "image_input": cost_info.image_input_cost if cost_info else None,
                "image_output": cost_info.image_output_cost if cost_info else None,
            }
            if cost_info
            else None,
            "prompt_text": prompt_text,
            "prompt_images": prompt_images,
        }
    )
    yield Put("gemini_api_calls", api_calls)

    if cost_info:
        current_total = yield Get("gemini_total_cost")
        new_total = (current_total or 0.0) + cost_info.total_cost
        yield Put("gemini_total_cost", new_total)

        model_cost_key = f"gemini_cost_{model}"
        current_model_cost = yield Get(model_cost_key)
        new_model_cost = (current_model_cost or 0.0) + cost_info.total_cost
        yield Put(model_cost_key, new_model_cost)

    return metadata


__all__ = [
    "GeminiClient",
    "get_gemini_client",
    "track_api_call",
]
