"""Shared type definitions for the Gemini integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class TokenUsage:
    """Token accounting returned by the Gemini API."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class APICallMetadata:
    """Metadata recorded for a Gemini API interaction."""

    operation: str
    model: str
    timestamp: datetime
    request_id: str | None
    latency_ms: float | None
    token_usage: TokenUsage | None
    error: str | None

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
        if self.error:
            metadata["error"] = self.error
        return metadata


__all__ = ["APICallMetadata", "TokenUsage"]
