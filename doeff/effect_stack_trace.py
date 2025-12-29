"""Rendering utilities for effect stack traces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff.types import EffectStackFrame, EffectStackFrameType, EffectStackTrace


@dataclass
class EffectStackTraceRenderer:
    """Renders EffectStackTrace for human consumption."""

    max_frames: int | None = None
    head_frames: int = 10

    def render(self, trace: EffectStackTrace) -> str:
        lines: list[str] = []
        exc_type = type(trace.original_exception).__name__
        exc_msg = str(trace.original_exception)
        lines.append(f"EffectError: {exc_type}: {exc_msg}")
        lines.append("")

        lines.append("Effect Call Stack (most recent call last):")
        lines.append("")

        frames, omitted = self._maybe_truncate(trace.frames)
        for frame in frames:
            self._render_frame(frame, lines)

        if omitted:
            lines.append(f"  ... ({omitted} frames omitted) ...")

        lines.append("")

        if trace.python_raise_location:
            loc = trace.python_raise_location
            lines.append("Exception raised at:")
            lines.append(
                f"  File \"{loc.filename}\", line {loc.line}, in {loc.function}"
            )
            if loc.code:
                lines.append(f"    {loc.code}")

        return "\n".join(lines)

    def _render_frame(self, frame: EffectStackFrame, lines: list[str]) -> None:
        prefix = "  "
        if frame.frame_type == EffectStackFrameType.KLEISLI_CALL:
            args_str = self._format_args(frame.call_args, frame.call_kwargs)
            lines.append(f"{prefix}-> {frame.name}({args_str})")
        elif frame.frame_type == EffectStackFrameType.EFFECT_YIELD:
            lines.append(f"{prefix}* yield {frame.name}")
        elif frame.frame_type == EffectStackFrameType.HANDLER_BOUNDARY:
            lines.append(f"{prefix}[handler: {frame.name}]")
        elif frame.frame_type == EffectStackFrameType.SPAWN_BOUNDARY:
            lines.append(f"{prefix}[spawn: {frame.name}]")
        else:
            lines.append(f"{prefix}.flat_map -> {frame.name}")

        if frame.location:
            lines.append(f"{prefix}  at {frame.location.filename}:{frame.location.line}")
            if frame.location.code:
                lines.append(f"{prefix}  | {frame.location.code}")

    def _format_args(
        self,
        call_args: tuple[Any, ...] | None,
        call_kwargs: dict[str, Any] | None,
    ) -> str:
        parts: list[str] = []
        if call_args:
            parts.extend(self._short_repr(arg) for arg in call_args)
        if call_kwargs:
            for key, value in call_kwargs.items():
                parts.append(f"{key}={self._short_repr(value)}")
        return ", ".join(parts)

    def _short_repr(self, value: Any, limit: int = 40) -> str:
        text = repr(value)
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def _maybe_truncate(
        self, frames: tuple[EffectStackFrame, ...]
    ) -> tuple[tuple[EffectStackFrame, ...], int]:
        if self.max_frames is None or len(frames) <= self.max_frames:
            return frames, 0

        tail_frames = max(0, self.max_frames - self.head_frames)
        omitted = len(frames) - self.max_frames
        display_frames = frames[: self.head_frames] + frames[-tail_frames:]
        return display_frames, omitted


__all__ = ["EffectStackTraceRenderer"]
