"""Unified VM trace entry and active-chain types."""


from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

TraceHandlerKind: TypeAlias = Literal["python", "rust_builtin"]
TraceDispatchAction: TypeAlias = Literal["active", "resumed", "transferred", "returned", "threw"]
HandlerStatusKind: TypeAlias = Literal[
    "active",
    "pending",
    "passed",
    "delegated",
    "resumed",
    "transferred",
    "returned",
    "threw",
]


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
    handler_kind: TraceHandlerKind
    source_file: str | None
    source_line: int | None


@dataclass(frozen=True)
class TraceDispatch:
    dispatch_id: int
    effect_repr: str
    handler_name: str
    handler_kind: TraceHandlerKind
    handler_source_file: str | None
    handler_source_line: int | None
    delegation_chain: tuple[TraceDelegationEntry, ...]
    action: TraceDispatchAction
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


@dataclass(frozen=True)
class HandlerStackEntry:
    handler_name: str
    handler_kind: TraceHandlerKind
    source_file: str | None
    source_line: int | None
    status: HandlerStatusKind


@dataclass(frozen=True)
class EffectResultActive:
    kind: Literal["active"] = "active"


@dataclass(frozen=True)
class EffectResultResumed:
    value_repr: str
    kind: Literal["resumed"] = "resumed"


@dataclass(frozen=True)
class EffectResultThrew:
    handler_name: str
    exception_repr: str
    kind: Literal["threw"] = "threw"


@dataclass(frozen=True)
class EffectResultTransferred:
    handler_name: str
    target_repr: str
    kind: Literal["transferred"] = "transferred"


EffectResult: TypeAlias = (
    EffectResultActive | EffectResultResumed | EffectResultThrew | EffectResultTransferred
)


@dataclass(frozen=True)
class ProgramYield:
    function_name: str
    source_file: str
    source_line: int
    sub_program_repr: str


@dataclass(frozen=True)
class EffectYield:
    function_name: str
    source_file: str
    source_line: int
    effect_repr: str
    handler_stack: tuple[HandlerStackEntry, ...]
    result: EffectResult


@dataclass(frozen=True)
class SpawnSite:
    function_name: str
    source_file: str
    source_line: int


@dataclass(frozen=True)
class SpawnBoundary:
    task_id: int
    parent_task: int | None
    spawn_site: SpawnSite | None


@dataclass(frozen=True)
class ContextEntry:
    data: Any


@dataclass(frozen=True)
class ExceptionSite:
    function_name: str
    source_file: str
    source_line: int
    exception_type: str
    message: str


ActiveChainEntry: TypeAlias = ProgramYield | EffectYield | SpawnBoundary | ContextEntry | ExceptionSite


def _coerce_handler_kind(value: Any) -> TraceHandlerKind:
    text = str(value)
    if text == "python":
        return "python"
    if text == "rust_builtin":
        return "rust_builtin"
    raise ValueError(f"Unknown handler kind: {text!r}")


def _coerce_dispatch_action(value: Any) -> TraceDispatchAction:
    text = str(value)
    if text == "active":
        return "active"
    if text == "resumed":
        return "resumed"
    if text == "transferred":
        return "transferred"
    if text == "returned":
        return "returned"
    if text == "threw":
        return "threw"
    raise ValueError(f"Unknown dispatch action: {text!r}")


def _coerce_handler_status(value: Any) -> HandlerStatusKind:
    text = str(value)
    lookup: dict[str, HandlerStatusKind] = {
        "active": "active",
        "pending": "pending",
        "passed": "passed",
        "delegated": "delegated",
        "resumed": "resumed",
        "transferred": "transferred",
        "returned": "returned",
        "threw": "threw",
    }
    if text in lookup:
        return lookup[text]
    raise ValueError(f"Unknown handler status: {text!r}")


def _coerce_delegation_entry(entry: Any) -> TraceDelegationEntry:
    if isinstance(entry, TraceDelegationEntry):
        return entry
    if isinstance(entry, dict):
        return TraceDelegationEntry(
            handler_name=str(entry.get("handler_name", "<handler>")),
            handler_kind=_coerce_handler_kind(entry.get("handler_kind", "rust_builtin")),
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
            handler_kind=_coerce_handler_kind(entry.get("handler_kind", "rust_builtin")),
            handler_source_file=entry.get("handler_source_file"),
            handler_source_line=entry.get("handler_source_line"),
            delegation_chain=chain,
            action=_coerce_dispatch_action(entry.get("action", "active")),
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


def _coerce_handler_stack_entry(entry: Any) -> HandlerStackEntry:
    if isinstance(entry, HandlerStackEntry):
        return entry
    if isinstance(entry, dict):
        return HandlerStackEntry(
            handler_name=str(entry.get("handler_name", "<handler>")),
            handler_kind=_coerce_handler_kind(entry.get("handler_kind", "rust_builtin")),
            source_file=entry.get("source_file"),
            source_line=entry.get("source_line"),
            status=_coerce_handler_status(entry.get("status", "active")),
        )
    raise TypeError(f"Unsupported handler stack entry type: {type(entry).__name__}")


def _coerce_effect_result(result: Any) -> EffectResult:
    if isinstance(
        result,
        (EffectResultActive, EffectResultResumed, EffectResultThrew, EffectResultTransferred),
    ):
        return result
    if not isinstance(result, dict):
        raise ValueError(f"Effect result payload must be dict, got {type(result).__name__}")

    kind = str(result.get("kind", "active"))
    if kind == "active":
        return EffectResultActive()
    if kind == "resumed":
        return EffectResultResumed(value_repr=str(result.get("value_repr", "None")))
    if kind == "threw":
        return EffectResultThrew(
            handler_name=str(result.get("handler_name", "<handler>")),
            exception_repr=str(result.get("exception_repr", "<exception>")),
        )
    if kind == "transferred":
        return EffectResultTransferred(
            handler_name=str(result.get("handler_name", "<handler>")),
            target_repr=str(result.get("target_repr", "<target>")),
        )
    raise ValueError(f"Unknown effect result kind: {kind!r}")


def _coerce_spawn_boundary_entry(entry: dict[str, Any]) -> SpawnBoundary:
    spawn_site_raw = entry.get("spawn_site")
    spawn_site: SpawnSite | None = None
    if isinstance(spawn_site_raw, dict):
        spawn_site = SpawnSite(
            function_name=str(spawn_site_raw.get("function_name", "<unknown>")),
            source_file=str(spawn_site_raw.get("source_file", "<unknown>")),
            source_line=int(spawn_site_raw.get("source_line", 0)),
        )
    parent_task_raw = entry.get("parent_task")
    parent_task = None if parent_task_raw is None else int(parent_task_raw)
    return SpawnBoundary(
        task_id=int(entry.get("task_id", 0)),
        parent_task=parent_task,
        spawn_site=spawn_site,
    )


def coerce_active_chain_entry(entry: Any) -> ActiveChainEntry:
    if isinstance(entry, (ProgramYield, EffectYield, SpawnBoundary, ContextEntry, ExceptionSite)):
        return entry
    if not isinstance(entry, dict):
        raise TypeError(f"Unsupported active-chain entry type: {type(entry).__name__}")

    kind = entry.get("kind")
    if kind == "program_yield":
        result: ActiveChainEntry = ProgramYield(
            function_name=str(entry.get("function_name", "<unknown>")),
            source_file=str(entry.get("source_file", "<unknown>")),
            source_line=int(entry.get("source_line", 0)),
            sub_program_repr=str(entry.get("sub_program_repr", "<sub_program>")),
        )
    elif kind == "effect_yield":
        stack_raw = entry.get("handler_stack", ())
        result = EffectYield(
            function_name=str(entry.get("function_name", "<unknown>")),
            source_file=str(entry.get("source_file", "<unknown>")),
            source_line=int(entry.get("source_line", 0)),
            effect_repr=str(entry.get("effect_repr", "<effect>")),
            handler_stack=tuple(_coerce_handler_stack_entry(item) for item in stack_raw),
            result=_coerce_effect_result(entry.get("result")),
        )
    elif kind == "spawn_boundary":
        result = _coerce_spawn_boundary_entry(entry)
    elif kind == "context_entry":
        result = ContextEntry(data=entry.get("data"))
    elif kind == "exception_site":
        result = ExceptionSite(
            function_name=str(entry.get("function_name", "<unknown>")),
            source_file=str(entry.get("source_file", "<unknown>")),
            source_line=int(entry.get("source_line", 0)),
            exception_type=str(entry.get("exception_type", "Exception")),
            message=str(entry.get("message", "")),
        )
    else:
        result = ContextEntry(data=entry)
    return result


def _entry_site(entry: ActiveChainEntry) -> tuple[str, str] | None:
    if isinstance(entry, ProgramYield):
        return (entry.function_name, entry.source_file)
    if isinstance(entry, EffectYield):
        return (entry.function_name, entry.source_file)
    if isinstance(entry, ExceptionSite):
        return (entry.function_name, entry.source_file)
    return None


def coerce_active_chain_entries(entries: list[Any] | tuple[Any, ...]) -> list[ActiveChainEntry]:
    coerced = [coerce_active_chain_entry(entry) for entry in entries]
    active_chain: list[ActiveChainEntry] = []
    context_boundaries: list[SpawnBoundary] = []
    seen_context_boundary_tasks: set[int] = set()

    for entry in coerced:
        if isinstance(entry, ContextEntry):
            payload = entry.data
            if isinstance(payload, dict) and payload.get("kind") == "spawn_boundary":
                boundary = _coerce_spawn_boundary_entry(payload)
                if boundary.task_id in seen_context_boundary_tasks:
                    continue
                seen_context_boundary_tasks.add(boundary.task_id)
                context_boundaries.append(boundary)
            else:
                active_chain.append(entry)
            continue
        active_chain.append(entry)

    for boundary in context_boundaries:
        if any(
            isinstance(item, SpawnBoundary) and item.task_id == boundary.task_id
            for item in active_chain
        ):
            continue
        insert_idx = next(
            (index for index, item in enumerate(active_chain) if isinstance(item, ExceptionSite)),
            len(active_chain),
        )
        if boundary.spawn_site is not None:
            boundary_site = (boundary.spawn_site.function_name, boundary.spawn_site.source_file)
            for index in range(len(active_chain) - 1, -1, -1):
                site = _entry_site(active_chain[index])
                if site == boundary_site:
                    insert_idx = index + 1
                    break
        active_chain.insert(insert_idx, boundary)

    return active_chain


__all__ = [
    "ActiveChainEntry",
    "ContextEntry",
    "EffectResult",
    "EffectResultActive",
    "EffectResultResumed",
    "EffectResultThrew",
    "EffectResultTransferred",
    "EffectYield",
    "ExceptionSite",
    "HandlerStackEntry",
    "ProgramYield",
    "SpawnBoundary",
    "SpawnSite",
    "TraceDelegationEntry",
    "TraceDispatch",
    "TraceEntry",
    "TraceFrame",
    "TraceResumePoint",
    "coerce_active_chain_entries",
    "coerce_active_chain_entry",
    "coerce_trace_entries",
    "coerce_trace_entry",
]
