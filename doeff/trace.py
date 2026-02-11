"""Unified VM trace entry types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias


@dataclass(frozen=True)
class TraceFrame:
    frame_id: int
    function_name: str
    source_file: str
    source_line: int
    args_repr: str | None


@dataclass(frozen=True)
class TraceDelegationEntry:
    handler_name: str
    handler_kind: Literal["python", "rust_builtin"]
    source_file: str | None
    source_line: int | None


@dataclass(frozen=True)
class TraceDispatch:
    dispatch_id: int
    effect_repr: str
    handler_name: str
    handler_kind: Literal["python", "rust_builtin"]
    handler_source_file: str | None
    handler_source_line: int | None
    delegation_chain: tuple[TraceDelegationEntry, ...]
    action: Literal["active", "resumed", "transferred", "returned", "threw"]
    value_repr: str | None
    exception_repr: str | None


@dataclass(frozen=True)
class TraceResumePoint:
    dispatch_id: int
    handler_name: str
    resumed_function_name: str
    source_file: str
    source_line: int
    value_repr: str | None


TraceEntry: TypeAlias = TraceFrame | TraceDispatch | TraceResumePoint


def _coerce_delegation_entry(entry: Any) -> TraceDelegationEntry:
    if isinstance(entry, TraceDelegationEntry):
        return entry
    if isinstance(entry, dict):
        return TraceDelegationEntry(
            handler_name=str(entry.get("handler_name", "<handler>")),
            handler_kind=str(entry.get("handler_kind", "rust_builtin")),  # type: ignore[arg-type]
            source_file=entry.get("source_file"),
            source_line=entry.get("source_line"),
        )
    raise TypeError(f"Unsupported delegation entry type: {type(entry).__name__}")


def coerce_trace_entry(entry: Any) -> TraceEntry:
    if isinstance(entry, (TraceFrame, TraceDispatch, TraceResumePoint)):
        return entry
    if not isinstance(entry, dict):
        raise TypeError(f"Unsupported trace entry type: {type(entry).__name__}")

    kind = entry.get("kind")
    if kind == "frame" or (
        "frame_id" in entry and "function_name" in entry and "effect_repr" not in entry
    ):
        return TraceFrame(
            frame_id=int(entry["frame_id"]),
            function_name=str(entry["function_name"]),
            source_file=str(entry["source_file"]),
            source_line=int(entry["source_line"]),
            args_repr=entry.get("args_repr"),
        )

    if kind == "dispatch" or "effect_repr" in entry:
        chain_raw = entry.get("delegation_chain", ())
        chain = tuple(_coerce_delegation_entry(item) for item in chain_raw)
        return TraceDispatch(
            dispatch_id=int(entry["dispatch_id"]),
            effect_repr=str(entry["effect_repr"]),
            handler_name=str(entry["handler_name"]),
            handler_kind=str(entry["handler_kind"]),  # type: ignore[arg-type]
            handler_source_file=entry.get("handler_source_file"),
            handler_source_line=entry.get("handler_source_line"),
            delegation_chain=chain,
            action=str(entry.get("action", "active")),  # type: ignore[arg-type]
            value_repr=entry.get("value_repr"),
            exception_repr=entry.get("exception_repr"),
        )

    if kind == "resume_point" or "resumed_function_name" in entry:
        return TraceResumePoint(
            dispatch_id=int(entry["dispatch_id"]),
            handler_name=str(entry["handler_name"]),
            resumed_function_name=str(entry["resumed_function_name"]),
            source_file=str(entry["source_file"]),
            source_line=int(entry["source_line"]),
            value_repr=entry.get("value_repr"),
        )

    raise ValueError(f"Unsupported trace entry payload: {entry!r}")


def coerce_trace_entries(entries: list[Any] | tuple[Any, ...]) -> list[TraceEntry]:
    return [coerce_trace_entry(entry) for entry in entries]


__all__ = [
    "TraceFrame",
    "TraceDelegationEntry",
    "TraceDispatch",
    "TraceResumePoint",
    "TraceEntry",
    "coerce_trace_entry",
    "coerce_trace_entries",
]
