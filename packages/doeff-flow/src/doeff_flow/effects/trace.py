"""Effect definitions for trace and observability operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class TraceEffectBase(EffectBase):
    """Base class for doeff-flow trace effects."""


@dataclass(frozen=True, kw_only=True)
class TracePush(TraceEffectBase):
    """Push a trace span."""

    name: str
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class TraceAnnotate(TraceEffectBase):
    """Annotate current trace span."""

    key: str
    value: Any


@dataclass(frozen=True, kw_only=True)
class TraceSnapshot(TraceEffectBase):
    """Capture a trace snapshot."""

    label: str


@dataclass(frozen=True, kw_only=True)
class TraceCapture(TraceEffectBase):
    """Capture trace output from the active trace handler."""

    format: str = "json"


__all__ = [
    "TraceAnnotate",
    "TraceCapture",
    "TraceEffectBase",
    "TracePush",
    "TraceSnapshot",
]
