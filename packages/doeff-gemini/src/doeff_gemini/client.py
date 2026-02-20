"""Gemini client helpers integrated with doeff effects."""

from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from doeff import (
    Ask,
    EffectGenerator,
    Get,
    Put,
    Tell,
    Try,
    do,
    slog,
)

from .costs import gemini_cost_calculator__default
from .types import APICallMetadata, CostInfo, GeminiCallResult, GeminiCostEstimate, TokenUsage


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


def _summarize_payload_for_log(value: Any, *, max_string_length: int = 200) -> Any:
    """Produce a repr-friendly copy of payload data with bounded string lengths."""

    if isinstance(value, str):
        if len(value) <= max_string_length:
            return value
        if value.startswith("data:") and ";base64," in value:
            header, encoded = value.split(";base64,", 1)
            return f"{header};base64,<len={len(encoded)}>"
        return f"{value[:max_string_length]}...<len={len(value)}>"

    if isinstance(value, dict):
        return {
            key: _summarize_payload_for_log(item, max_string_length=max_string_length)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            _summarize_payload_for_log(item, max_string_length=max_string_length) for item in value
        ]

    if isinstance(value, tuple):
        return tuple(
            _summarize_payload_for_log(item, max_string_length=max_string_length) for item in value
        )

    return value


@dataclass(slots=True)
class GeminiAPIPayloadLog:
    """Log wrapper that keeps full payload data with a repr-safe preview."""

    operation: str
    model: str
    payload: Any
    preview_payload: Any | None = None
    max_string_length: int = 200

    def __post_init__(self) -> None:
        if self.preview_payload is None:
            self.preview_payload = self.payload

    def _summarized_preview(self) -> Any:
        try:
            return _summarize_payload_for_log(
                self.preview_payload, max_string_length=self.max_string_length
            )
        except Exception:  # pragma: no cover - defensive fallback
            return repr(self.preview_payload)

    def __repr__(self) -> str:  # pragma: no cover - trivial formatting
        preview = self._summarized_preview()
        return (
            "GeminiAPIPayloadLog("
            f"operation={self.operation!r}, "
            f"model={self.model!r}, "
            f"payload={preview!r})"
        )

    __str__ = __repr__


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
def _result_ok(value: Any) -> EffectGenerator[Any]:
    """Wrap a value as an Ok result via the Try effect."""
    return value


@do
def _result_err(exc: Exception) -> EffectGenerator[Any]:
    """Wrap an exception as an Err result via the Try effect."""
    raise exc
    yield  # type: ignore[misc]  # unreachable, keeps generator typing valid


@do
def get_gemini_client() -> EffectGenerator[GeminiClient]:
    """Retrieve a configured :class:`GeminiClient` from Reader or State effects."""

    @do
    def ask(name: str):
        return (yield Ask(name))

    @do
    def ask_optional(name: str) -> EffectGenerator[Any]:
        safe_result = yield Try(ask(name))
        return safe_result.value if safe_result.is_ok() else None

    safe_client = yield Try(ask("gemini_client"))
    client = safe_client.value if safe_client.is_ok() else None
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
            yield Tell("google-auth is not installed; install google-auth or set GEMINI API key")
            raise exc

        try:
            adc_credentials, adc_project = google_auth_default()
        except DefaultCredentialsError as exc:
            yield Tell(
                "Failed to load Google Application Default Credentials. "
                "Run 'gcloud auth application-default login' or provide a GEMINI API key."
            )
            raise exc

        yield Tell("Using Google Application Default Credentials for Gemini client")

        if adc_project is None and project is None:
            yield Tell(
                "Google credentials found but project ID missing. Set 'gemini_project' or configure gcloud."
            )
            raise ValueError("Google project ID could not be determined.")

        if credentials is None:
            credentials = adc_credentials
        if project is None:
            project = adc_project
        if vertexai is None:
            vertexai = True
    else:
        if vertexai is None:
            vertexai = False
        yield Tell("Using Gemini API key authentication")

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
        derived_total = (
            sum(
                token
                for token in [input_tokens, output_tokens, image_input_tokens, image_output_tokens]
                if token is not None
            )
            or 0
        )

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
    api_payload: Any | None = None,
) -> EffectGenerator[APICallMetadata]:
    """Log and persist observability metadata for a Gemini API invocation.

    The optional ``api_payload`` parameter allows callers to attach the exact
    payload passed to the Google client while the log preview remains sanitized.
    """
    end_time = time.time()
    latency_ms = (end_time - start_time) * 1000

    sanitized_payload, prompt_text, prompt_images = _prepare_request_payload(request_payload)

    token_usage = _extract_usage(response) if response and not error else None
    request_id = _extract_request_id(response) if response else None

    @do
    def _invoke_cost_calculator(
        calculator, call_result: GeminiCallResult
    ) -> EffectGenerator[GeminiCostEstimate]:
        if calculator is None:
            raise ValueError("gemini_cost_calculator is missing")

        if not callable(calculator):
            raise TypeError(
                "gemini_cost_calculator must be a KleisliProgram[GeminiCallResult, GeminiCostEstimate]"
            )

        estimate = yield calculator(call_result)

        if estimate is None:
            raise ValueError("gemini_cost_calculator returned None")

        if not isinstance(estimate, GeminiCostEstimate):
            raise TypeError("gemini_cost_calculator must return GeminiCostEstimate")

        return estimate

    @do
    def _build_cost_input() -> EffectGenerator[GeminiCallResult]:
        usage_for_cost = token_usage.to_cost_usage() if token_usage else None
        payload = {
            "operation": operation,
            "request_summary": request_summary,
            "request_payload": sanitized_payload,
            "api_payload": api_payload if api_payload is not None else request_payload,
            "usage": usage_for_cost,
            "start_time": start_time,
            "end_time": end_time,
            "latency_ms": latency_ms,
            "prompt_text": prompt_text,
            "prompt_images": prompt_images,
        }
        if token_usage:
            payload["token_usage"] = token_usage.to_dict()
        if error is None:
            result_for_cost = yield Try(_result_ok(response))
        else:
            result_for_cost = yield Try(_result_err(error))
        return GeminiCallResult(
            model_name=model,
            payload=payload,
            result=result_for_cost,
        )

    cost_info: CostInfo | None = None
    if token_usage:
        call_result = yield _build_cost_input()

        safe_calculator = yield Try(Ask("gemini_cost_calculator"))
        calculator = safe_calculator.value if safe_calculator.is_ok() else None

        calculator_errors: list[str] = []

        estimate: GeminiCostEstimate | None = None
        if calculator is not None:
            safe_estimate = yield Try(_invoke_cost_calculator(calculator, call_result))
            if safe_estimate.is_ok():
                estimate = safe_estimate.value
            else:
                calculator_errors.append(str(safe_estimate.error))

        if estimate is None:
            safe_default_estimate = yield Try(
                _invoke_cost_calculator(gemini_cost_calculator__default, call_result)
            )
            if safe_default_estimate.is_ok():
                estimate = safe_default_estimate.value
            else:
                calculator_errors.append(str(safe_default_estimate.error))

        if estimate is None:
            message = (
                "Failed to calculate Gemini cost. Provide a gemini_cost_calculator "
                "KleisliProgram[GeminiCallResult, GeminiCostEstimate] via Ask('gemini_cost_calculator'), "
                "or ensure gemini_cost_calculator__default can handle this model. "
                f"Errors: {calculator_errors}"
            )
            raise RuntimeError(message)
        else:
            cost_info = estimate.cost_info

    metadata = APICallMetadata(
        operation=operation,
        model=model,
        timestamp=datetime.now(timezone.utc),  # nosemgrep: doeff-no-datetime-now-in-do
        request_id=request_id,
        latency_ms=latency_ms,
        token_usage=token_usage,
        cost_info=cost_info,
        error=str(error) if error else None,
    )

    if error:
        yield slog(
            msg=f"Gemini API error: operation={operation}, model={model}, latency={latency_ms:.2f}ms, error={error}",
            level="error",
            error=error,
        )
    else:
        log_line = (
            f"Gemini API call: operation={operation}, model={model}, latency={latency_ms:.2f}ms"
        )
        if token_usage:
            log_line += f", tokens={token_usage.total_tokens}"
        if cost_info:
            log_line += f", cost=${cost_info.total_cost:.6f}"
        yield slog(msg=log_line)

    yield Tell(
        GeminiAPIPayloadLog(
            operation=operation,
            model=model,
            payload=api_payload if api_payload is not None else request_payload,
            preview_payload=sanitized_payload,
        )
    )

    call_entry = {
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

    safe_calls = yield Try(Get("gemini_api_calls"))
    current_calls = safe_calls.value if safe_calls.is_ok() else []
    entries = list(current_calls) if isinstance(current_calls, list) else []
    entries.append(call_entry)
    yield Put("gemini_api_calls", entries)

    if cost_info:
        model_cost_key = f"gemini_cost_{model}"
        safe_total = yield Try(Get("gemini_total_cost"))
        current_total = safe_total.value if safe_total.is_ok() else 0.0
        total_base = current_total if isinstance(current_total, (int, float)) else 0.0
        yield Put("gemini_total_cost", total_base + cost_info.total_cost)

        safe_model_total = yield Try(Get(model_cost_key))
        current_model_total = safe_model_total.value if safe_model_total.is_ok() else 0.0
        model_base = current_model_total if isinstance(current_model_total, (int, float)) else 0.0
        yield Put(model_cost_key, model_base + cost_info.total_cost)

    return metadata


__all__ = [
    "GeminiClient",
    "get_gemini_client",
    "track_api_call",
]
