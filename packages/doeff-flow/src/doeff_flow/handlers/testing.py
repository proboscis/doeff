"""Testing handlers for doeff-flow trace effects."""


import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from doeff import Effect, Pass, Resume, do
from doeff_flow.effects import TraceAnnotate, TraceCapture, TracePush, TraceSnapshot

ProtocolHandler = Callable[[Any, Any], Any]


@dataclass
class _Span:
    name: str
    metadata: dict[str, Any]


@dataclass
class MockTraceRecorder:
    """In-memory recorder used by mock trace handlers."""

    workflow_id: str = "mock-workflow"
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    _step: int = 0
    _spans: list[_Span] = field(default_factory=list)
    _annotations: dict[str, Any] = field(default_factory=dict)
    _entries: list[dict[str, Any]] = field(default_factory=list)

    def push(self, effect: TracePush) -> None:
        metadata = dict(effect.metadata or {})
        self._spans.append(_Span(name=effect.name, metadata=metadata))
        self._annotations.update(metadata)
        self._record(current_effect=f"TracePush({effect.name!r})")

    def annotate(self, effect: TraceAnnotate) -> None:
        self._annotations[effect.key] = effect.value
        self._record(current_effect=f"TraceAnnotate({effect.key!r})")

    def snapshot(self, effect: TraceSnapshot) -> None:
        self._record(current_effect=f"TraceSnapshot({effect.label!r})", label=effect.label)

    def capture(self, output_format: str) -> str | list[dict[str, Any]]:
        if output_format == "json":
            return json.dumps(self._entries)
        if output_format == "jsonl":
            return "\n".join(json.dumps(entry) for entry in self._entries)
        if output_format in {"dict", "python"}:
            return [dict(entry) for entry in self._entries]
        raise ValueError(f"Unsupported TraceCapture format: {output_format!r}")

    def _record(self, *, current_effect: str, label: str | None = None) -> None:
        self._step += 1
        now = datetime.now(timezone.utc).isoformat()

        last_slog: dict[str, Any] | None = None
        if label is not None or self._annotations:
            last_slog = {}
            if label is not None:
                last_slog["label"] = label
            if self._annotations:
                last_slog["annotations"] = dict(self._annotations)

        self._entries.append(
            {
                "workflow_id": self.workflow_id,
                "step": self._step,
                "status": "running",
                "current_effect": current_effect,
                "trace": [
                    {
                        "function": span.name,
                        "file": "mock://trace",
                        "line": 0,
                        "code": json.dumps(span.metadata, sort_keys=True) if span.metadata else None,
                    }
                    for span in self._spans
                ],
                "started_at": self.started_at,
                "updated_at": now,
                "error": None,
                "result": None,
                "gather": None,
                "last_slog": last_slog,
            }
        )


def mock_handlers(
    *,
    recorder: MockTraceRecorder | None = None,
) -> ProtocolHandler:
    """Build an in-memory mock protocol handler for trace effects."""

    active_recorder = recorder or MockTraceRecorder()

    @do
    def handler(effect: Effect, k: Any):
        if isinstance(effect, TracePush):
            active_recorder.push(effect)
            return (yield Resume(k, None))
        if isinstance(effect, TraceAnnotate):
            active_recorder.annotate(effect)
            return (yield Resume(k, None))
        if isinstance(effect, TraceSnapshot):
            active_recorder.snapshot(effect)
            return (yield Resume(k, None))
        if isinstance(effect, TraceCapture):
            captured = active_recorder.capture(effect.format)
            return (yield Resume(k, captured))
        return (yield Pass())

    return handler


__all__ = [
    "MockTraceRecorder",
    "ProtocolHandler",
    "mock_handlers",
]
