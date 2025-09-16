"""Shared type definitions for the Gemini integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class TokenUsage:
    """Token accounting returned by the Gemini API."""

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


@dataclass(frozen=True)
class APICallMetadata:
    """Metadata recorded for a Gemini API interaction."""

    operation: str
    model: str
    timestamp: datetime
    request_id: Optional[str]
    latency_ms: Optional[float]
    token_usage: Optional[TokenUsage]
    error: Optional[str]

    def to_graph_metadata(self) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {
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


__all__ = ["TokenUsage", "APICallMetadata"]
