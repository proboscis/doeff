"""Shared type definitions for the Gemini integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from doeff import Result


@dataclass(frozen=True)
class GeminiImageEditResult:
    """Result payload returned by the Gemini image edit helper."""

    image_bytes: bytes
    mime_type: str
    text: str | None

    def to_pil_image(self):  # type: ignore[override]
        """Convert the response payload into a :class:`PIL.Image.Image`."""
        from io import BytesIO

        from PIL import Image

        with BytesIO(self.image_bytes) as buffer:
            image = Image.open(buffer)
            return image.copy()

    def save(self, path: str, *, format: str | None = None) -> None:
        """Persist the edited image to disk."""

        image = self.to_pil_image()
        image.save(path, format=format)


@dataclass(frozen=True)
class TokenUsage:
    """Token accounting returned by the Gemini API."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    text_input_tokens: int | None = None
    text_output_tokens: int | None = None
    image_input_tokens: int | None = None
    image_output_tokens: int | None = None

    def to_cost_usage(self) -> dict[str, int] | None:
        usage: dict[str, int] = {}
        if self.text_input_tokens is not None:
            usage["text_input_tokens"] = self.text_input_tokens
        if self.text_output_tokens is not None:
            usage["text_output_tokens"] = self.text_output_tokens
        if self.image_input_tokens is not None:
            usage["image_input_tokens"] = self.image_input_tokens
        if self.image_output_tokens is not None:
            usage["image_output_tokens"] = self.image_output_tokens
        if self.total_tokens is not None:
            usage["total_tokens"] = self.total_tokens
        return usage or None

    def to_dict(self) -> dict[str, int | None]:
        """Return a serializable snapshot of usage counters."""

        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "text_input_tokens": self.text_input_tokens,
            "text_output_tokens": self.text_output_tokens,
            "image_input_tokens": self.image_input_tokens,
            "image_output_tokens": self.image_output_tokens,
        }


@dataclass(frozen=True)
class CostInfo:
    """Detailed cost breakdown for a Gemini API call."""

    total_cost: float
    text_input_cost: float
    text_output_cost: float
    image_input_cost: float
    image_output_cost: float

    def to_dict(self) -> dict[str, float]:
        """Return a serializable snapshot of the cost breakdown."""

        return {
            "total_cost": self.total_cost,
            "text_input_cost": self.text_input_cost,
            "text_output_cost": self.text_output_cost,
            "image_input_cost": self.image_input_cost,
            "image_output_cost": self.image_output_cost,
        }


@dataclass(frozen=True)
class APICallMetadata:
    """Metadata recorded for a Gemini API interaction."""

    operation: str
    model: str
    timestamp: datetime
    request_id: str | None
    latency_ms: float | None
    token_usage: TokenUsage | None
    cost_info: 'CostInfo' | None = None
    error: str | None = None

    def to_graph_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "type": "gemini_api_call",
            "operation": self.operation,
            "model": self.model,
            "timestamp": self.timestamp.isoformat(),
        }
        if self.request_id:
            metadata["request_id"] = self.request_id
        if self.latency_ms is not None:
            metadata["latency_ms"] = self.latency_ms
        if self.token_usage:
            metadata.update(
                {
                    "input_tokens": self.token_usage.input_tokens,
                    "output_tokens": self.token_usage.output_tokens,
                    "total_tokens": self.token_usage.total_tokens,
                }
            )
        if self.cost_info:
            metadata["cost_usd"] = self.cost_info.total_cost
        if self.error:
            metadata["error"] = self.error
        return metadata


@dataclass(frozen=True)
class GeminiCallResult:
    """Context passed to cost calculators for a Gemini API call."""

    model_name: str
    payload: dict[str, Any]
    result: Result[Any]


@dataclass(frozen=True)
class GeminiCostEstimate:
    """Cost calculation result produced by a Gemini cost calculator."""

    cost_info: CostInfo
    raw_usage: dict[str, Any] | None = None


__all__ = [
    "APICallMetadata",
    "TokenUsage",
    "CostInfo",
    "GeminiImageEditResult",
    "GeminiCallResult",
    "GeminiCostEstimate",
]
