"""Debug utilities for inspecting CESK execution state.

Provides utilities for formatting and inspecting the K (continuation) stack,
effect call trees, and Kleisli call chains during program execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff.cesk.frames import Kontinuation


@dataclass(frozen=True)
class KleisliStackEntry:
    """Single entry in the Kleisli call stack."""

    function_name: str
    filename: str
    lineno: int
    depth: int

    def format_location(self) -> str:
        return f"{self.filename}:{self.lineno}"


@dataclass(frozen=True)
class EffectCallNode:
    """Node in the effect call tree."""

    function_name: str
    filename: str
    lineno: int
    effect_type: str | None
    children: tuple[EffectCallNode, ...]

    def format_tree(self, indent: int = 0) -> str:
        prefix = "  " * indent
        if self.effect_type:
            line = f"{prefix}└─ {self.function_name}()\n{prefix}   └─ {self.effect_type}"
        else:
            line = f"{prefix}└─ {self.function_name}()"

        if self.children:
            child_lines = [child.format_tree(indent + 1) for child in self.children]
            return line + "\n" + "\n".join(child_lines)
        return line


@dataclass(frozen=True)
class KFrameInfo:
    """Information about a single K frame."""

    frame_type: str
    description: str
    depth: int


@dataclass(frozen=True)
class DebugContext:
    """Complete debug context at a point in execution."""

    kleisli_stack: tuple[KleisliStackEntry, ...]
    k_frames: tuple[KFrameInfo, ...]
    effect_call_tree: EffectCallNode | None
    current_effect: str | None

    def format_kleisli_stack(self) -> str:
        if not self.kleisli_stack:
            return "Kleisli Call Stack:\n  (empty)"
        lines = ["Kleisli Call Stack:"]
        for entry in self.kleisli_stack:
            lines.append(f"  [{entry.depth}] {entry.function_name} ({entry.format_location()})")
        return "\n".join(lines)

    def format_k_frames(self) -> str:
        if not self.k_frames:
            return "K Frame Stack:\n  (empty)"
        lines = ["K Frame Stack:"]
        for frame in self.k_frames:
            lines.append(f"  [{frame.depth}] {frame.frame_type}({frame.description})")
        return "\n".join(lines)

    def format_effect_tree(self) -> str:
        if self.effect_call_tree is None:
            return "Effect Call Stack:\n  (no effects)"
        lines = ["Effect Call Stack:"]
        lines.append(self.effect_call_tree.format_tree(1))
        return "\n".join(lines)

    def format_all(self) -> str:
        sections = [
            self.format_effect_tree(),
            "",
            self.format_kleisli_stack(),
            "",
            self.format_k_frames(),
        ]
        if self.current_effect:
            sections.insert(0, f"Current Effect: {self.current_effect}\n")
        return "\n".join(sections)


def extract_kleisli_stack(k: Kontinuation) -> tuple[KleisliStackEntry, ...]:
    """Extract Kleisli (@do function) entries from ReturnFrame kleisli fields."""
    from doeff.cesk.frames import ReturnFrame

    entries: list[KleisliStackEntry] = []
    for frame in k:
        if isinstance(frame, ReturnFrame) and frame.kleisli_function_name is not None:
            entries.append(
                KleisliStackEntry(
                    function_name=frame.kleisli_function_name,
                    filename=frame.kleisli_filename or "<unknown>",
                    lineno=frame.kleisli_lineno or 0,
                    depth=len(entries),
                )
            )
    return tuple(entries)


def describe_k_frame(frame: object) -> tuple[str, str]:
    """Return (frame_type, description) for any K frame. Centralized to avoid DRY violations."""
    from doeff.cesk.frames import (
        AskLazyFrame,
        GatherFrame,
        GatherWaiterFrame,
        GraphCaptureFrame,
        InterceptBypassFrame,
        InterceptFrame,
        ListenFrame,
        LocalFrame,
        RaceFrame,
        RaceWaiterFrame,
        ReturnFrame,
        SafeFrame,
    )
    from doeff.cesk.handler_frame import HandlerFrame, HandlerResultFrame

    if isinstance(frame, ReturnFrame):
        pc = frame.program_call
        name = pc.function_name if pc else "<generator>"
        if frame.kleisli_function_name:
            return ("ReturnFrame", f"kleisli={frame.kleisli_function_name}")
        return ("ReturnFrame", f"continuation={name}")
    if isinstance(frame, HandlerFrame):
        handler_name = getattr(frame.handler, "__name__", "<handler>")
        return ("HandlerFrame", f"handler={handler_name}")
    if isinstance(frame, HandlerResultFrame):
        effect_name = type(frame.original_effect).__name__
        return ("HandlerResultFrame", f"effect={effect_name}")
    if isinstance(frame, LocalFrame):
        return ("LocalFrame", "env restore")
    if isinstance(frame, SafeFrame):
        return ("SafeFrame", "error boundary")
    if isinstance(frame, ListenFrame):
        return ("ListenFrame", f"log_start={frame.log_start_index}")
    if isinstance(frame, GatherFrame):
        remaining = len(frame.remaining_programs)
        collected = len(frame.collected_results)
        return ("GatherFrame", f"remaining={remaining}, collected={collected}")
    if isinstance(frame, InterceptFrame):
        return ("InterceptFrame", f"transforms={len(frame.transforms)}")
    if isinstance(frame, GraphCaptureFrame):
        return ("GraphCaptureFrame", f"start_index={frame.graph_start_index}")
    if isinstance(frame, AskLazyFrame):
        return ("AskLazyFrame", f"key={frame.ask_key!r}")
    if isinstance(frame, RaceFrame):
        return ("RaceFrame", f"tasks={len(frame.task_ids)}")
    if isinstance(frame, GatherWaiterFrame):
        return ("GatherWaiterFrame", "waiting")
    if isinstance(frame, RaceWaiterFrame):
        return ("RaceWaiterFrame", "waiting")
    if isinstance(frame, InterceptBypassFrame):
        return ("InterceptBypassFrame", "bypass")
    return (type(frame).__name__, "")


def extract_k_frame_info(k: Kontinuation) -> tuple[KFrameInfo, ...]:
    """Extract information about all frames in the K stack."""
    infos: list[KFrameInfo] = []
    for i, frame in enumerate(k):
        frame_type, description = describe_k_frame(frame)
        infos.append(KFrameInfo(frame_type=frame_type, description=description, depth=i))
    return tuple(infos)


def build_effect_call_tree(k: Kontinuation, current_effect: str | None = None) -> EffectCallNode | None:
    """Build effect call tree from K stack using ReturnFrame.program_call as source."""
    from doeff.cesk.frames import ReturnFrame

    kleisli_entries: list[tuple[str, str, int]] = []
    for frame in reversed(k):
        if isinstance(frame, ReturnFrame) and frame.program_call:
            pc = frame.program_call
            created_at = getattr(pc, "created_at", None)
            if created_at:
                kleisli_entries.append((pc.function_name, created_at.filename, created_at.line))

    if not kleisli_entries:
        return None

    def build_nested(entries: list[tuple[str, str, int]], idx: int) -> EffectCallNode:
        name, filename, lineno = entries[idx]
        if idx == len(entries) - 1:
            return EffectCallNode(
                function_name=name,
                filename=filename,
                lineno=lineno,
                effect_type=current_effect,
                children=(),
            )
        child = build_nested(entries, idx + 1)
        return EffectCallNode(
            function_name=name,
            filename=filename,
            lineno=lineno,
            effect_type=None,
            children=(child,),
        )

    return build_nested(kleisli_entries, 0)


def get_debug_context(k: Kontinuation, current_effect: str | None = None) -> DebugContext:
    """Build complete debug context from K stack."""
    return DebugContext(
        kleisli_stack=extract_kleisli_stack(k),
        k_frames=extract_k_frame_info(k),
        effect_call_tree=build_effect_call_tree(k, current_effect),
        current_effect=current_effect,
    )


def format_k_stack(k: Kontinuation) -> str:
    """Format K stack for human-readable output."""
    ctx = get_debug_context(k)
    return ctx.format_k_frames()


def format_kleisli_stack(k: Kontinuation) -> str:
    """Format Kleisli call stack for human-readable output."""
    ctx = get_debug_context(k)
    return ctx.format_kleisli_stack()


def format_effect_call_tree(k: Kontinuation, current_effect: str | None = None) -> str:
    """Format effect call tree for human-readable output."""
    ctx = get_debug_context(k, current_effect)
    return ctx.format_effect_tree()


__all__ = [
    "DebugContext",
    "EffectCallNode",
    "KFrameInfo",
    "KleisliStackEntry",
    "build_effect_call_tree",
    "describe_k_frame",
    "extract_k_frame_info",
    "extract_kleisli_stack",
    "format_effect_call_tree",
    "format_k_stack",
    "format_kleisli_stack",
    "get_debug_context",
]
