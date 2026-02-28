"""Production handlers for doeff-flow trace effects."""


import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doeff import Effect, Pass, Resume, do
from doeff_flow.effects import TraceAnnotate, TraceCapture, TracePush, TraceSnapshot
from doeff_flow.trace import (
    LiveTrace,
    TraceFrame,
    _write_trace,
    get_default_trace_dir,
    validate_workflow_id,
)

ProtocolHandler = Callable[[Any, Any], Any]


@dataclass
class _Span:
    name: str
    metadata: dict[str, Any]


@dataclass
class ProductionTraceRecorder:
    """Recorder used by production trace effect handlers."""

    workflow_id: str
    trace_file: Path
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    _step: int = 0
    _spans: list[_Span] = field(default_factory=list)
    _annotations: dict[str, Any] = field(default_factory=dict)
    _entries: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        workflow_id: str,
        trace_dir: Path | str | None = None,
    ) -> ProductionTraceRecorder:
        validated_workflow_id = validate_workflow_id(workflow_id)
        if trace_dir is None:
            resolved_trace_dir = get_default_trace_dir()
        elif isinstance(trace_dir, str):
            resolved_trace_dir = Path(trace_dir)
        else:
            resolved_trace_dir = trace_dir

        trace_file = resolved_trace_dir / validated_workflow_id / "trace.jsonl"
        trace_file.parent.mkdir(parents=True, exist_ok=True)
        return cls(workflow_id=validated_workflow_id, trace_file=trace_file)

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
        slog_payload: dict[str, Any] | None = None
        if label is not None or self._annotations:
            slog_payload = {}
            if label is not None:
                slog_payload["label"] = label
            if self._annotations:
                slog_payload["annotations"] = dict(self._annotations)

        trace = LiveTrace(
            workflow_id=self.workflow_id,
            step=self._step,
            status="running",
            current_effect=current_effect,
            trace=self._build_frames(),
            started_at=self.started_at,
            updated_at=now,
            error=None,
            result=None,
            gather=None,
            last_slog=slog_payload,
        )
        _write_trace(self.trace_file, trace)
        self._entries.append(_trace_to_dict(trace))

    def _build_frames(self) -> list[TraceFrame]:
        frames: list[TraceFrame] = []
        for span in self._spans:
            metadata = json.dumps(span.metadata, sort_keys=True) if span.metadata else None
            frames.append(
                TraceFrame(
                    function=span.name,
                    file=str(self.trace_file),
                    line=0,
                    code=metadata,
                )
            )
        return frames


def _trace_to_dict(trace: LiveTrace) -> dict[str, Any]:
    return {
        "workflow_id": trace.workflow_id,
        "step": trace.step,
        "status": trace.status,
        "current_effect": trace.current_effect,
        "trace": [asdict(frame) for frame in trace.trace],
        "started_at": trace.started_at,
        "updated_at": trace.updated_at,
        "error": trace.error,
        "result": trace.result,
        "gather": asdict(trace.gather) if trace.gather else None,
        "last_slog": trace.last_slog,
    }


def production_handlers(
    workflow_id: str,
    trace_dir: Path | str | None = None,
    *,
    recorder: ProductionTraceRecorder | None = None,
) -> ProtocolHandler:
    """Build a protocol handler for real trace recording."""

    active_recorder = recorder or ProductionTraceRecorder.create(workflow_id=workflow_id, trace_dir=trace_dir)

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
    "ProductionTraceRecorder",
    "ProtocolHandler",
    "production_handlers",
]
