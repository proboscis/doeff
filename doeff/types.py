"""
Core types for the doeff effects system.

This module contains the foundational types with zero internal dependencies.
"""

from __future__ import annotations

import json
import traceback
from pprint import pformat
from dataclasses import dataclass, field, fields, replace
from functools import wraps
from typing import (
    Any,
    Callable,
    Dict,
    Generator,
    Generic,
    List,
    Optional,
    Protocol,
    TypeVar,
    Union,
    TYPE_CHECKING,
    runtime_checkable,
)

# Import Program for type alias, but avoid circular imports
if TYPE_CHECKING:
    from doeff.program import Program
    from phart.styles import NodeStyle

# Re-export vendored types for backward compatibility
from doeff._vendor import (
    TraceError,
    trace_err,
    Ok,
    Err,
    Result,
    Maybe,
    Nothing,
    NOTHING,
    Some,
    WNode,
    WStep,
    WGraph,
    FrozenDict,
)

# Type variables
T = TypeVar("T")
U = TypeVar("U")

# ============================================
# Effect Creation Context
# ============================================

@dataclass(frozen=True)
class EffectCreationContext:
    """Context information about where an effect was created."""
    
    filename: str
    line: int
    function: str
    code: str | None = None
    stack_trace: List[Dict[str, Any]] = field(default_factory=list)
    frame_info: Any = None  # FrameInfo from inspect module
    
    def format_location(self) -> str:
        """Format the creation location as a string."""
        return f'{self.filename}:{self.line} in {self.function}'
    
    def format_full(self) -> str:
        """Format the full creation context with stack trace."""
        lines = []
        lines.append(f"Effect created at {self.format_location()}")
        if self.code:
            lines.append(f"    {self.code}")
        if self.stack_trace:
            lines.append("\nCreation stack trace:")
            for frame in self.stack_trace:
                lines.append(f'  File "{frame["filename"]}", line {frame["line"]}, in {frame["function"]}')
                if frame.get("code"):
                    lines.append(f'    {frame["code"]}')
        return "\n".join(lines)
    
    def build_traceback(self) -> str:
        """Build a detailed traceback-style output from stored frame information."""
        lines = []
        lines.append("Traceback (most recent call last):")
        
        # Add frames from stack_trace in reverse order (innermost last)
        if self.stack_trace:
            for frame in reversed(self.stack_trace):
                filename = frame.get('filename', '<unknown>')
                line_no = frame.get('line', 0)
                func_name = frame.get('function', '<unknown>')
                code = frame.get('code', '')
                
                lines.append(f'  File "{filename}", line {line_no}, in {func_name}')
                if code:
                    lines.append(f'    {code}')
        
        # Add the immediate creation location
        lines.append(f'  File "{self.filename}", line {self.line}, in {self.function}')
        if self.code:
            lines.append(f'    {self.code}')
        
        return "\n".join(lines)

    def without_frames(self) -> "EffectCreationContext":
        """Return a sanitized copy without live frame references."""

        sanitized_stack: list[dict[str, Any]] = []
        for frame in self.stack_trace:
            if isinstance(frame, dict):
                sanitized_frame = {
                    key: value
                    for key, value in frame.items()
                    if key != "frame"
                }
                sanitized_stack.append(sanitized_frame)

        return EffectCreationContext(
            filename=self.filename,
            line=self.line,
            function=self.function,
            code=self.code,
            stack_trace=sanitized_stack,
            frame_info=None,
        )


# ============================================
# Traceback Capture Helpers
# ============================================

_TRACEBACK_ATTR = "_doeff_traceback"


@dataclass(frozen=True)
class CapturedTraceback:
    """Structured representation of a traceback captured from an exception."""

    traceback: traceback.TracebackException

    def _raw_lines(self) -> list[str]:
        lines: list[str] = []
        for chunk in self.traceback.format():
            text = chunk.rstrip("\n")
            if not text:
                continue
            lines.extend(part for part in text.split("\n"))
        return [line for line in lines if line]

    def _sanitize_lines(self) -> list[str]:
        lines = self._raw_lines()
        if not lines:
            return []

        sanitized: list[str] = []
        seen_headers = 0
        skip_effect_failure = False

        for line in lines:
            stripped = line.strip()

            if stripped.startswith("Traceback (most recent call last):"):
                seen_headers += 1
                if seen_headers == 1:
                    sanitized.append(line)
                continue

            if stripped == "----- Exception Traceback -----":
                continue

            if "doeff.types.EffectFailure" in stripped:
                skip_effect_failure = True
                continue

            if skip_effect_failure:
                continue

            sanitized.append(line)

        return sanitized

    def lines(
        self,
        *,
        condensed: bool = False,
        max_lines: int = 12,
    ) -> list[str]:
        """Return sanitized traceback lines with optional condensation."""

        sanitized = self._sanitize_lines()
        if not condensed or max_lines is None or len(sanitized) <= max_lines:
            return sanitized

        if not sanitized:
            return []

        header: list[str] = []
        body = sanitized

        if sanitized[0].strip().startswith("Traceback"):
            header = [sanitized[0]]
            body = sanitized[1:]

        if not body:
            return header

        available = max_lines - len(header)
        if available <= 0:
            return header[:max_lines]

        body_tail = body[-available:]
        return header + body_tail

    def format(
        self,
        *,
        condensed: bool = False,
        max_lines: int = 12,
    ) -> str:
        """Render the traceback as a single string."""

        return "\n".join(self.lines(condensed=condensed, max_lines=max_lines))


def capture_traceback(exc: BaseException) -> CapturedTraceback:
    """Capture and memoize traceback information for an exception."""

    tb_exc = traceback.TracebackException.from_exception(exc, capture_locals=False)
    captured = CapturedTraceback(tb_exc)
    setattr(exc, _TRACEBACK_ATTR, captured)
    return captured


def get_captured_traceback(exc: BaseException) -> CapturedTraceback | None:
    """Return previously captured traceback information if available."""

    return getattr(exc, _TRACEBACK_ATTR, None)


# ============================================
# Effect Failure Exception
# ============================================

@dataclass
class EffectFailure(Exception):
    """Complete error information for a failed effect.

    Combines both the runtime traceback (where error occurred) and
    creation context (where effect was created) into a single clean structure.
    """

    effect: "Effect"
    cause: BaseException  # The original exception that caused the failure
    runtime_traceback: CapturedTraceback | None = None  # Runtime stack trace where error occurred
    creation_context: Optional[EffectCreationContext] = None  # Where the effect was created

    def __str__(self) -> str:
        """Format the error for display."""
        lines = [f"Effect '{self.effect.__class__.__name__}' failed"]

        # Add creation location if available
        if self.creation_context:
            lines.append(f"Created at: {self.creation_context.format_location()}")

        # Add the cause
        lines.append(f"Caused by: {self.cause.__class__.__name__}: {self.cause}")

        return "\n".join(lines)
    
    def __post_init__(self):
        """Capture runtime traceback if not provided."""
        if self.creation_context is None:
            self.creation_context = getattr(self.effect, "created_at", None)

        if self.runtime_traceback is None and isinstance(self.cause, BaseException):
            captured = get_captured_traceback(self.cause)
            if captured is None:
                captured = capture_traceback(self.cause)
            self.runtime_traceback = captured


# ============================================
# Core Effect Type
# ============================================

E = TypeVar("E", bound="EffectBase")


@runtime_checkable
class Effect(Protocol):
    """Protocol implemented by all effect values."""

    created_at: Optional[EffectCreationContext]

    def intercept(
        self, transform: Callable[["Effect"], "Effect | Program"]
    ) -> "Effect":
        """Return a copy where any nested programs are intercepted."""

    def with_created_at(self: E, created_at: Optional[EffectCreationContext]) -> E:
        """Return a copy with updated creation context."""


@dataclass(frozen=True, kw_only=True)
class EffectBase:
    """Base dataclass implementing :class:`Effect` semantics."""

    created_at: Optional[EffectCreationContext] = field(
        default=None, compare=False
    )

    def intercept(
        self: E, transform: Callable[[Effect], Effect | "Program"]
    ) -> E:
        updates: Dict[str, Any] = {}
        changed = False
        for f in fields(self):
            if f.name == "created_at":
                continue
            value = getattr(self, f.name)
            new_value = _intercept_value(value, transform)
            if new_value is not value:
                changed = True
            updates[f.name] = new_value
        if not changed:
            return self
        return replace(self, **updates)

    def with_created_at(
        self: E, created_at: Optional[EffectCreationContext]
    ) -> E:
        if created_at is self.created_at:
            return self
        return replace(self, created_at=created_at)


# Type alias for generators used in @do functions
# This simplifies the verbose Generator[Union[Effect, Program], Any, T] pattern
if TYPE_CHECKING:
    EffectGenerator = Generator[Union[Effect, "Program"], Any, T]
else:
    # Runtime version to avoid importing Program
    EffectGenerator = Generator[Union[Effect, Any], Any, T]


# ============================================
# Program Type
# ============================================

# The core monad - a generator that yields Effects and returns a value
ProgramGenerator = Generator[Effect, Any, T]


# ============================================
# Execution Context
# ============================================

@dataclass
class ExecutionContext:
    """
    Execution context for the pragmatic engine.

    Tracks mutable state throughout program execution.
    """

    # Reader environment
    env: Dict[str, Any] = field(default_factory=dict)
    # State storage
    state: Dict[str, Any] = field(default_factory=dict)
    # Writer log
    log: List[Any] = field(default_factory=list)
    # Computation graph
    graph: WGraph = field(default_factory=lambda: WGraph.single(None))
    # IO permission flag
    io_allowed: bool = True
    # Memo storage (shared across parallel executions)
    cache: Dict[str, Any] = field(default_factory=dict)
    # Observed effects during run (shared reference)
    effect_observations: list["EffectObservation"] = field(default_factory=list)

    def copy(self) -> ExecutionContext:
        """Create a shallow copy of the context."""
        return ExecutionContext(
            env=self.env.copy(),
            state=self.state.copy(),
            log=self.log.copy(),
            graph=self.graph,
            io_allowed=self.io_allowed,
            cache=self.cache,  # Cache is shared reference, not copied
            effect_observations=self.effect_observations,
        )

    def with_env_update(self, updates: Dict[str, Any]) -> ExecutionContext:
        """Create a new context with updated environment."""
        new_env = self.env.copy()
        new_env.update(updates)
        return ExecutionContext(
            env=new_env,
            state=self.state.copy(),
            log=self.log.copy(),
            graph=self.graph,
            io_allowed=self.io_allowed,
            cache=self.cache,  # Cache is shared
            effect_observations=self.effect_observations,
        )


@dataclass(frozen=True)
class EffectObservation:
    """Lightweight record of an observed effect during execution."""

    effect_type: str
    key: str | None
    context: EffectCreationContext | None = None


# ============================================
# Failure Introspection Types
# ============================================


@dataclass(frozen=True)
class EffectFailureInfo:
    """Summary of a single EffectFailure instance within an error chain."""

    effect: Effect
    creation_context: EffectCreationContext | None
    cause: BaseException | None
    runtime_trace: CapturedTraceback | None
    cause_trace: CapturedTraceback | None


@dataclass(frozen=True)
class ExceptionFailureInfo:
    """Summary of a plain exception in the failure chain."""

    exception: BaseException
    trace: CapturedTraceback | None


FailureEntry = EffectFailureInfo | ExceptionFailureInfo


@dataclass(frozen=True)
class RunFailureDetails:
    """Structured view of the failure chain for a RunResult."""

    entries: tuple[FailureEntry, ...]

    @classmethod
    def from_error(cls, error: Any) -> "RunFailureDetails | None":
        if not isinstance(error, BaseException):
            return None

        entries: list[FailureEntry] = []
        seen: set[int] = set()
        seen_exceptions: set[int] = set()

        def capture(exc: BaseException | None) -> CapturedTraceback | None:
            if exc is None:
                return None
            captured = get_captured_traceback(exc)
            if captured is None:
                captured = capture_traceback(exc)
            return captured

        def walk(exc: BaseException) -> None:
            exc_id = id(exc)
            if exc_id in seen:
                return
            seen.add(exc_id)

            if isinstance(exc, EffectFailure):
                runtime_trace = exc.runtime_traceback
                cause_exc: BaseException | None = exc.cause if exc.cause else None
                if cause_exc is not None:
                    seen_exceptions.add(id(cause_exc))
                entries.append(
                    EffectFailureInfo(
                        effect=exc.effect,
                        creation_context=exc.creation_context,
                        cause=cause_exc,
                        runtime_trace=runtime_trace,
                        cause_trace=capture(cause_exc),
                    )
                )
                if isinstance(exc.cause, BaseException):
                    walk(exc.cause)
            else:
                if exc_id in seen_exceptions:
                    return
                entries.append(
                    ExceptionFailureInfo(
                        exception=exc,
                        trace=capture(exc),
                    )
                )
                seen_exceptions.add(exc_id)
                if exc.__cause__ is not None:
                    walk(exc.__cause__)

        walk(error)

        if not entries:
            return None

        return cls(entries=tuple(entries))


# ============================================
# Run Result
# ============================================

@dataclass(frozen=True)
class RunResult(Generic[T]):
    """
    Result from running a Program through the pragmatic engine.

    Contains both the execution context (state, log, graph) and the computation result.
    """

    context: ExecutionContext
    result: Result[T]

    @property
    def value(self) -> T:
        """Get the successful value or raise an exception."""
        if isinstance(self.result, Ok):
            return self.result.value
        else:
            raise self.result.error
    
    @property
    def is_ok(self) -> bool:
        """Check if the result is successful."""
        return isinstance(self.result, Ok)
    
    @property
    def is_err(self) -> bool:
        """Check if the result is an error."""
        return isinstance(self.result, Err)

    @property
    def env(self) -> Dict[str, Any]:
        """Get the final environment."""
        return self.context.env

    @property
    def state(self) -> Dict[str, Any]:
        """Get the final state."""
        return self.context.state

    @property
    def shared_state(self) -> Dict[str, Any]:
        """Get shared atomic state captured during execution."""
        store = self.context.cache.get("__atomic_state__")
        if isinstance(store, dict):
            return store
        return {}

    @property
    def log(self) -> List[Any]:
        """Get the accumulated log."""
        return self.context.log

    @property
    def graph(self) -> WGraph:
        """Get the computation graph."""
        return self.context.graph

    @property
    def effect_observations(self) -> list["EffectObservation"]:
        """Get recorded effect observations."""
        return self.context.effect_observations

    def _failure_details(self) -> RunFailureDetails | None:
        if not self.is_err:
            return None
        return RunFailureDetails.from_error(self.result.error)

    def _failure_summary(self) -> str:
        details = self._failure_details()
        if details and details.entries:
            head = details.entries[0]
            if isinstance(head, EffectFailureInfo):
                effect_name = head.effect.__class__.__name__
                if head.cause:
                    return (
                        f"effect={effect_name}, "
                        f"cause={head.cause.__class__.__name__}: {head.cause}"
                    )
                return f"effect={effect_name}"
            if isinstance(head, ExceptionFailureInfo):
                exc = head.exception
                return f"{exc.__class__.__name__}: {exc}"
        return repr(self.result.error)

    def format_error(self, *, condensed: bool = False) -> str:
        """Return a formatted traceback for the failure if present."""
        if self.is_ok:
            return ""

        error = self.result.error

        if isinstance(error, TraceError):
            return str(error)

        if isinstance(error, EffectFailure):
            trace = error.runtime_traceback
            if trace is not None:
                return trace.format(condensed=condensed)
            if isinstance(error.cause, BaseException):
                captured = get_captured_traceback(error.cause)
                if captured is None:
                    captured = capture_traceback(error.cause)
                return captured.format(condensed=condensed)
            return f"EffectFailure: {error}"

        if isinstance(error, BaseException):
            captured = get_captured_traceback(error)
            if captured is None:
                captured = capture_traceback(error)
            return captured.format(condensed=condensed)

        return str(error)

    @property
    def formatted_error(self) -> str:
        """Get formatted error string if result is a failure."""
        return self.format_error()

    def __repr__(self) -> str:
        if self.is_ok:
            return (
                f"RunResult(Ok({repr(self.result.value)}), "
                f"state={len(self.state)} items, "
                f"log={len(self.log)} entries)"
            )

        summary = self._failure_summary()
        return (
            f"RunResult(Err({summary}), "
            f"state={len(self.state)} items, "
            f"log={len(self.log)} entries)"
        )

    def display(self, verbose: bool = False, indent: int = 2) -> str:
        """Render a human-readable report using structured sections."""

        context = RunResultDisplayContext(
            run_result=self,
            verbose=verbose,
            indent_unit=" " * indent,
            failure_details=self._failure_details(),
        )
        renderer = RunResultDisplayRenderer(context)
        return renderer.render()

    def visualize_graph_ascii(
        self,
        *,
        node_style: "NodeStyle | str" = "square",
        node_spacing: int = 4,
        margin: int = 1,
        layer_spacing: int = 2,
        show_arrows: bool = True,
        use_ascii: bool | None = None,
        max_value_length: int = 32,
        include_ops: bool = True,
        custom_decorators: Dict[WNode | str, tuple[str, str]] | None = None,
    ) -> str:
        """Render the computation graph as ASCII art using the phart library.

        Args:
            node_style: NodeStyle enum or string name recognised by phart.
            node_spacing: Minimum horizontal spacing between nodes.
            margin: Padding applied to the rendered canvas.
            layer_spacing: Vertical spacing between layers.
            show_arrows: Whether to render arrow heads on edges.
            use_ascii: Force ASCII-only characters when True.
            max_value_length: Maximum characters for node value previews.
            include_ops: Append producing op metadata when available.
            custom_decorators: Optional mapping of nodes or labels to (prefix, suffix).

        Returns:
            ASCII diagram of the run graph.

        Raises:
            ImportError: If phart (and its dependencies) are not installed.
            ValueError: If ``node_style`` does not resolve to a valid style.
            TypeError: If ``custom_decorators`` keys are not ``WNode`` or ``str``.
        """

        try:
            import networkx as nx
            from phart.renderer import ASCIIRenderer
            from phart.styles import LayoutOptions, NodeStyle
        except ImportError as exc:  # pragma: no cover - missing optional dependency
            raise ImportError(
                "visualize_graph_ascii requires the phart package. Install it via `pip install phart`."
            ) from exc

        steps = self.graph.steps or frozenset({self.graph.last})

        base_graph = nx.DiGraph()
        producers: Dict[WNode, WStep] = {}

        for step in steps:
            producers[step.output] = step
            node_meta = step.meta or {}
            base_graph.add_node(
                step.output,
                value=step.output.value,
                op=node_meta.get("op"),
            )
            for input_node in step.inputs:
                if input_node not in base_graph:
                    base_graph.add_node(input_node, value=input_node.value)
                base_graph.add_edge(input_node, step.output, op=node_meta.get("op"))

        def _preview(value: Any) -> str:
            preview = repr(value)
            preview = preview.replace("\n", " ").replace("\r", " ")
            if len(preview) <= max_value_length:
                return preview
            suffix = "..."
            slice_len = max(0, max_value_length - len(suffix))
            return f"{preview[:slice_len]}{suffix}"

        try:
            ordering = list(nx.topological_sort(base_graph))
        except nx.NetworkXUnfeasible:
            ordering = list(base_graph.nodes)

        label_map: Dict[WNode, str] = {}
        for index, node in enumerate(ordering):
            node_data = base_graph.nodes[node]
            preview = _preview(node_data.get("value"))
            op_label = None
            if include_ops:
                producer = producers.get(node)
                if producer:
                    op_label = (producer.meta or {}).get("op")
            parts = [f"{index:02d}", preview]
            if op_label:
                parts.append(f"@{op_label}")
            label_map[node] = " ".join(part for part in parts if part)

        phart_graph = nx.DiGraph()
        for node, label in label_map.items():
            phart_graph.add_node(label)

        for src, dst in base_graph.edges:
            phart_graph.add_edge(label_map[src], label_map[dst])

        if isinstance(node_style, str):
            try:
                resolved_style = NodeStyle[node_style.upper()]
            except KeyError as err:
                valid = ", ".join(style.name.lower() for style in NodeStyle)
                raise ValueError(
                    f"Unknown node_style '{node_style}'. Valid values: {valid}."
                ) from err
        else:
            resolved_style = node_style

        decorator_map: Dict[str, tuple[str, str]] | None = None
        if custom_decorators:
            decorator_map = {}
            for key, decorators in custom_decorators.items():
                if isinstance(key, WNode):
                    target_label = label_map.get(key)
                elif isinstance(key, str):
                    target_label = key
                else:
                    raise TypeError("custom_decorators keys must be WNode or str")
                if target_label:
                    decorator_map[target_label] = decorators
            if decorator_map and resolved_style is not NodeStyle.CUSTOM:
                resolved_style = NodeStyle.CUSTOM

        options = LayoutOptions(
            node_spacing=node_spacing,
            margin=margin,
            layer_spacing=layer_spacing,
            node_style=resolved_style,
            show_arrows=show_arrows,
            use_ascii=use_ascii,
            custom_decorators=decorator_map,
        )

        renderer = ASCIIRenderer(phart_graph, options=options)
        return renderer.render()

    def _format_value(self, value: Any, indent: int, max_length: int = 200) -> str:
        """Format a value for display, handling various types."""
        if value is None:
            return "None"
        elif isinstance(value, bool):
            return str(value)
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            if len(value) > max_length:
                return f'"{value[:max_length]}..."'
            return f'"{value}"'
        elif isinstance(value, dict):
            if not value:
                return "{}"
            try:
                json_str = json.dumps(value, indent=None, default=str)
                if len(json_str) > max_length:
                    # Show keys only if too long
                    keys = list(value.keys())[:5]
                    keys_str = ", ".join(f'"{k}"' for k in keys)
                    if len(value) > 5:
                        keys_str += f", ... ({len(value) - 5} more)"
                    return f"{{{keys_str}}}"
                return json_str
            except:
                return f"<dict with {len(value)} items>"
        elif isinstance(value, list):
            if not value:
                return "[]"
            if len(value) > 5:
                return f"[{len(value)} items]"
            try:
                json_str = json.dumps(value, indent=None, default=str)
                if len(json_str) > max_length:
                    return f"[{len(value)} items]"
                return json_str
            except:
                return f"<list with {len(value)} items>"
        elif hasattr(value, "__class__"):
            class_name = value.__class__.__name__
            if hasattr(value, "__repr__"):
                repr_str = repr(value)
                if len(repr_str) > max_length:
                    return f"<{class_name} object>"
                return repr_str
            return f"<{class_name} object>"
        else:
            str_val = str(value)
            if len(str_val) > max_length:
                return str_val[:max_length] + "..."
            return str_val

@dataclass(frozen=True)
class RunResultDisplayContext:
    """Shared context for building RunResult display output."""

    run_result: "RunResult[Any]"
    verbose: bool
    indent_unit: str
    failure_details: RunFailureDetails | None

    def indent(self, level: int, text: str) -> str:
        if not text:
            return text
        return f"{self.indent_unit * level}{text}"


@dataclass(frozen=True)
class _BaseSection:
    context: RunResultDisplayContext

    def indent(self, level: int, text: str) -> str:
        return self.context.indent(level, text)

    def format_value(self, value: Any, *, max_length: int = 200) -> str:
        indent_width = len(self.context.indent_unit) or 2
        return self.context.run_result._format_value(
            value,
            indent_width,
            max_length=max_length,
        )


class _HeaderSection(_BaseSection):
    def render(self) -> list[str]:
        return ["=" * 60, "RunResult Internal Data", "=" * 60]


class _StatusSection(_BaseSection):
    def render(self) -> list[str]:
        rr = self.context.run_result
        lines = ["📊 Result Status:"]

        if rr.is_ok:
            lines.append(self.indent(1, "✅ Success"))
            value_repr = pformat(rr.result.value, width=80, compact=False)
            if "\n" in value_repr:
                lines.append(self.indent(1, "Value:"))
                for line in value_repr.splitlines():
                    lines.append(self.indent(2, line))
            else:
                lines.append(self.indent(1, f"Value: {value_repr}"))
            return lines

        lines.append(self.indent(1, "❌ Failure"))
        details = self.context.failure_details
        if details and details.entries:
            head = details.entries[0]
            if isinstance(head, EffectFailureInfo):
                effect_name = head.effect.__class__.__name__
                lines.append(self.indent(1, f"Effect '{effect_name}' failed"))
                if head.creation_context:
                    lines.append(
                        self.indent(
                            2,
                            f"📍 Created at: {head.creation_context.format_location()}",
                        )
                    )
                if head.cause:
                    if isinstance(head.cause, EffectFailure):
                        lines.append(
                            self.indent(
                                2,
                                "Caused by: EffectFailure (see error chain)",
                            )
                        )
                    else:
                        lines.append(
                            self.indent(
                                2,
                                f"Caused by: {head.cause.__class__.__name__}: {head.cause}",
                            )
                        )
            elif isinstance(head, ExceptionFailureInfo):
                exc = head.exception
                lines.append(
                    self.indent(1, f"{exc.__class__.__name__}: {exc}")
                )
        else:
            lines.append(self.indent(1, f"Error: {rr.result.error!r}"))

        return lines


class _ErrorSection(_BaseSection):
    def render(self) -> list[str]:
        details = self.context.failure_details
        if details is None or not details.entries:
            return []

        lines: list[str] = ["Error Chain (most recent first):"]

        for idx, entry in enumerate(details.entries, start=1):
            if idx > 1:
                lines.append("")

            if isinstance(entry, EffectFailureInfo):
                lines.extend(self._render_effect_entry(idx, entry))
            elif isinstance(entry, ExceptionFailureInfo):
                lines.extend(self._render_exception_entry(idx, entry))

        return lines

    def _render_effect_entry(
        self, idx: int, entry: EffectFailureInfo
    ) -> list[str]:
        effect_name = entry.effect.__class__.__name__
        lines = [self.indent(1, f"[{idx}] Effect '{effect_name}' failed")]

        ctx = entry.creation_context
        if ctx is not None:
            lines.append(self.indent(2, f"📍 Created at: {ctx.format_location()}"))
            if ctx.code:
                lines.append(self.indent(3, ctx.code))
            if self.context.verbose and ctx.stack_trace:
                lines.append(self.indent(2, "📍 Effect Creation Stack Trace:"))
                for frame_line in ctx.build_traceback().splitlines():
                    lines.append(self.indent(3, frame_line))
        else:
            lines.append(self.indent(2, "📍 Created at: <unknown>"))

        if entry.cause:
            if isinstance(entry.cause, EffectFailure):
                lines.append(
                    self.indent(
                        2,
                        "Caused by: EffectFailure (see nested entries)",
                    )
                )
            else:
                lines.append(
                    self.indent(
                        2,
                        f"Caused by: {entry.cause.__class__.__name__}: {entry.cause}",
                    )
                )
                if entry.cause_trace:
                    lines.extend(
                        self._render_trace(
                            entry.cause_trace,
                            "🔥 Cause Stack Trace",
                        )
                    )

        if entry.runtime_trace:
            lines.extend(
                self._render_trace(
                    entry.runtime_trace,
                    "🔥 Execution Stack Trace",
                )
            )

        return lines

    def _render_exception_entry(
        self, idx: int, entry: ExceptionFailureInfo
    ) -> list[str]:
        exc = entry.exception
        lines = [self.indent(1, f"[{idx}] {exc.__class__.__name__}: {exc}")]
        if entry.trace:
            lines.extend(self._render_trace(entry.trace, "Stack"))
        return lines

    def _render_trace(
        self,
        trace: CapturedTraceback,
        label: str,
    ) -> list[str]:
        condensed = not self.context.verbose
        frames = trace.lines(condensed=condensed)
        if not frames:
            return []
        result = [self.indent(2, f"{label}:")]
        result.extend(self.indent(3, frame) for frame in frames)
        return result


class _StateSection(_BaseSection):
    def render(self) -> list[str]:
        rr = self.context.run_result
        lines = ["🗂️ State:"]
        if rr.state:
            items = list(rr.state.items())
            for key, value in items[:20]:
                value_str = self.format_value(value, max_length=100)
                lines.append(self.indent(1, f"{key}: {value_str}"))
            if len(items) > 20:
                remaining = len(items) - 20
                lines.append(self.indent(1, f"... and {remaining} more items"))
        else:
            lines.append(self.indent(1, "(empty)"))
        return lines


class _SharedStateSection(_BaseSection):
    def render(self) -> list[str]:
        shared = self.context.run_result.shared_state
        if not shared:
            return []

        lines = ["🤝 Shared State:"]
        items = list(shared.items())
        for key, value in items[:20]:
            value_str = self.format_value(value, max_length=100)
            lines.append(self.indent(1, f"{key}: {value_str}"))
        if len(items) > 20:
            remaining = len(items) - 20
            lines.append(self.indent(1, f"... and {remaining} more items"))
        return lines


class _LogSection(_BaseSection):
    def render(self) -> list[str]:
        rr = self.context.run_result
        lines = ["📝 Logs:"]
        if rr.log:
            for index, entry in enumerate(rr.log[:10]):
                entry_str = self.format_value(entry, max_length=150)
                lines.append(self.indent(1, f"[{index}] {entry_str}"))
            if len(rr.log) > 10:
                remaining = len(rr.log) - 10
                lines.append(self.indent(1, f"... and {remaining} more entries"))
        else:
            lines.append(self.indent(1, "(no logs)"))
        return lines


class _EffectUsageSection(_BaseSection):
    def render(self) -> list[str]:
        rr = self.context.run_result
        lines = ["🔗 Dep/Ask Usage:"]
        observations = [
            obs
            for obs in rr.effect_observations
            if obs.effect_type in {"Dep", "Ask"}
        ]
        if not observations:
            lines.append(self.indent(1, "(no Dep/Ask effects observed)"))
            return lines

        limit = 40
        for idx, obs in enumerate(observations[:limit], start=1):
            key_text = f" key={obs.key!r}" if obs.key is not None else ""
            lines.append(self.indent(1, f"[{idx}] {obs.effect_type}{key_text}"))
            if obs.context is not None:
                lines.append(
                    self.indent(2, obs.context.format_location())
                )
                if obs.context.code:
                    lines.append(self.indent(3, obs.context.code))
            else:
                lines.append(self.indent(2, "<location unavailable>"))

        if len(observations) > limit:
            remaining = len(observations) - limit
            lines.append(self.indent(1, f"... and {remaining} more entries"))

        return lines


class _GraphSection(_BaseSection):
    def render(self) -> list[str]:
        rr = self.context.run_result
        graph = rr.graph
        lines = ["🌳 Graph:"]

        if not graph or not graph.steps:
            lines.append(self.indent(1, "(no graph steps)"))
            return lines

        lines.append(self.indent(1, f"Steps: {len(graph.steps)}"))
        if not self.context.verbose:
            return lines

        steps = list(graph.steps)
        for index, step in enumerate(steps[:5]):
            lines.append(self.indent(1, f"Step {index}:"))
            if step.meta:
                meta_str = self.format_value(step.meta, max_length=100)
                lines.append(self.indent(2, f"Meta: {meta_str}"))
            lines.append(self.indent(2, f"Inputs: {len(step.inputs)} nodes"))
            output_value = step.output.value
            output_cls = output_value.__class__.__name__ if output_value else "None"
            lines.append(self.indent(2, f"Output: {output_cls}"))

        if len(steps) > 5:
            remaining = len(steps) - 5
            lines.append(self.indent(1, f"... and {remaining} more steps"))

        return lines


class _EnvironmentSection(_BaseSection):
    def render(self) -> list[str]:
        if not self.context.verbose:
            return []
        env = self.context.run_result.env
        if not env:
            return []
        lines = ["🌍 Environment:"]
        items = list(env.items())
        for key, value in items[:10]:
            value_str = self.format_value(value, max_length=100)
            lines.append(self.indent(1, f"{key}: {value_str}"))
        if len(items) > 10:
            remaining = len(items) - 10
            lines.append(self.indent(1, f"... and {remaining} more items"))
        return lines


class _SummarySection(_BaseSection):
    def render(self) -> list[str]:
        rr = self.context.run_result
        lines = ["=" * 60, "Summary:"]
        status = "✅ OK" if rr.is_ok else "❌ Error"
        lines.append(f"  • Status: {status}")
        lines.append(f"  • State items: {len(rr.state)}")
        shared_items = len(rr.shared_state)
        if shared_items:
            lines.append(f"  • Shared state items: {shared_items}")
        lines.append(f"  • Log entries: {len(rr.log)}")
        graph_steps = len(rr.graph.steps) if rr.graph else 0
        lines.append(f"  • Graph steps: {graph_steps}")
        lines.append(f"  • Environment vars: {len(rr.env)}")
        lines.append("=" * 60)
        return lines


@dataclass(frozen=True)
class RunResultDisplayRenderer:
    """Assemble display sections into the final string."""

    context: RunResultDisplayContext

    def render(self) -> str:
        sections = [
            _HeaderSection(self.context),
            _StatusSection(self.context),
            _ErrorSection(self.context),
            _StateSection(self.context),
            _SharedStateSection(self.context),
            _LogSection(self.context),
            _EffectUsageSection(self.context),
            _GraphSection(self.context),
            _EnvironmentSection(self.context),
            _SummarySection(self.context),
        ]

        collected: list[str] = []
        for section in sections:
            section_lines = section.render()
            if not section_lines:
                continue
            if collected:
                collected.append("")
            collected.extend(section_lines)

        return "\n".join(collected)


# ============================================
# Listen Result
# ============================================

@dataclass(frozen=True)
class ListenResult:
    """Result from writer.listen effect."""

    value: Any
    log: List[Any]
    
    def __iter__(self):
        """Make ListenResult unpackable as a tuple (value, log)."""
        return iter([self.value, self.log])


def _intercept_value(value: Any, transform: Callable[[Effect], Effect | "Program"]) -> Any:
    """Recursively intercept Programs embedded within ``value``."""

    from doeff.program import Program  # Local import to avoid circular dependency
    #from loguru import logger

    if isinstance(value, Program):
        #logger.info(f"Intercepting Program: {value}")
        return value.intercept(transform)

    if isinstance(value, Effect):
        #logger.info(f"Intercepting Effect: {value}")
        return value.intercept(transform)

    if isinstance(value, dict):
        changed = False
        new_items: Dict[Any, Any] = {}
        for key, item in value.items():
            new_item = _intercept_value(item, transform)
            if new_item is not item:
                changed = True
            new_items[key] = new_item
        if not changed:
            return value
        return new_items

    if isinstance(value, tuple):
        new_items = tuple(_intercept_value(item, transform) for item in value)
        if new_items == value:
            return value
        return new_items

    if isinstance(value, list):
        return [_intercept_value(item, transform) for item in value]

    if isinstance(value, set):
        return {_intercept_value(item, transform) for item in value}

    if isinstance(value, frozenset):
        return frozenset(_intercept_value(item, transform) for item in value)

    if callable(value):
        return _wrap_callable(value, transform)

    return value


def _wrap_callable(
    func: Callable[..., Any], transform: Callable[[Effect], Effect | "Program"]
):
    """Wrap callable so that any Program it returns is intercepted."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = func(*args, **kwargs)
        return _intercept_value(result, transform)

    return wrapper


__all__ = [
    # Vendored types
    "TraceError",
    "trace_err",
    "Ok",
    "Err",
    "Result",
    "Maybe",
    "Nothing",
    "NOTHING",
    "Some",
    "WNode",
    "WStep",
    "WGraph",
    "FrozenDict",
    # Core types
    "Effect",
    "EffectGenerator",
    "Program",
    "ExecutionContext",
    "EffectObservation",
    "RunResult",
    "ListenResult",
]
