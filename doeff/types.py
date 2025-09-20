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
    runtime_traceback: str | None = None  # Runtime stack trace where error occurred
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

        if self.runtime_traceback is None and self.cause:
            # Capture the runtime traceback from the cause
            self.runtime_traceback = "".join(
                traceback.format_exception(
                    self.cause.__class__,
                    self.cause,
                    self.cause.__traceback__
                )
            )


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


# ============================================
# Effect Generator Type Alias
# ============================================

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

    def __repr__(self) -> str:
        if self.is_ok:
            return (
                "RunResult(ok=True, value="
                f"{self._format_value(self.result.value, indent=2, max_length=60)})"
            )

        error_chain, _ = self._extract_error_chain(self.result.error)
        if error_chain:
            head = error_chain[0]
            if head["type"] == "effect":
                location = (
                    head["context"].format_location()
                    if head.get("context")
                    else "<unknown>"
                )
                cause = head.get("cause")
                cause_str = f"{cause.__class__.__name__}: {cause}" if cause else "<no cause>"
                return (
                    "RunResult(ok=False, effect="
                    f"{head['tag']}, location={location}, cause={cause_str})"
                )
            if head["type"] == "exception":
                return (
                    "RunResult(ok=False, exception="
                    f"{head['class']}: {head['message']})"
                )

        return f"RunResult(ok=False, error={self.result.error!r})"

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

    def format_error(self) -> str:
        """Format error with full traceback if result is a failure."""
        if isinstance(self.result, Ok):
            return ""
        
        error = self.result.error
        
        # If it's a TraceError, it already has formatted traceback
        if isinstance(error, TraceError):
            return str(error)
        
        # Otherwise, format the exception
        if isinstance(error, BaseException):
            # Try to get traceback if available
            if hasattr(error, "__traceback__") and error.__traceback__:
                tb_lines = traceback.format_exception(
                    type(error), error, error.__traceback__
                )
                return "".join(tb_lines)
            else:
                # No traceback available, just show the error
                return f"{error.__class__.__name__}: {error}"
        
        # For non-exception errors, just convert to string
        return str(error)
    
    @property
    def formatted_error(self) -> str:
        """Get formatted error string if result is a failure."""
        return self.format_error()
    
    def __repr__(self) -> str:
        """Show enhanced representation with formatted traceback for failures."""
        if isinstance(self.result, Ok):
            return (
                f"RunResult(Ok({repr(self.result.value)}), "
                f"state={len(self.state)} items, "
                f"log={len(self.log)} entries)"
            )
        else:
            # Format the error with traceback
            error_str = self.format_error()
            # Indent the error for better readability
            indented_error = "\n  ".join(error_str.split("\n"))
            return (
                f"RunResult(Err:\n  {indented_error}\n"
                f"  state={len(self.state)} items, "
                f"  log={len(self.log)} entries)"
            )
    
    def _format_execution_traceback(self, traceback_text: str, verbose: bool = False) -> List[str]:
        """Format execution traceback for display.
        
        Args:
            traceback_text: Raw traceback text from TraceError
            verbose: If True, show full traceback; if False, show condensed version
            
        Returns:
            List of formatted lines to display
        """
        if not traceback_text:
            return []
        
        tb_lines = traceback_text.strip().split("\n")
        
        # Clean up the traceback - remove duplicate headers and EffectFailure noise
        cleaned_lines = []
        seen_headers = 0
        skip_remaining = False
        i = 0
        
        while i < len(tb_lines):
            line = tb_lines[i]
            
            # Handle duplicate traceback headers
            if "Traceback (most recent call last):" in line:
                seen_headers += 1
                if seen_headers == 1:
                    cleaned_lines.append(line)
                i += 1
                continue
            
            # Skip "Exception Traceback" dividers
            if "----- Exception Traceback -----" in line:
                i += 1
                continue
            
            # Stop processing at EffectFailure (that's our wrapper, not the real error)
            if "doeff.types.EffectFailure:" in line:
                # But keep "The above exception was the direct cause" if present
                if i > 0 and "The above exception was the direct cause" in tb_lines[i-1]:
                    pass  # Already added
                skip_remaining = True
                i += 1
                continue
            
            # Skip everything after EffectFailure
            if skip_remaining:
                i += 1
                continue
            
            # Keep all other lines (frames, error messages, etc.)
            cleaned_lines.append(line)
            i += 1
        
        if not verbose:
            # In non-verbose mode, show only the most relevant frames
            result = []
            
            # Find where the actual frames start and end
            frame_start = -1
            frame_end = -1
            for i, line in enumerate(cleaned_lines):
                if line.strip().startswith("File "):
                    if frame_start == -1:
                        frame_start = i
                    frame_end = i + 1  # Include the code line after File
                    
            # Include header
            for line in cleaned_lines:
                if "Traceback" in line:
                    result.append(line)
                    break
            
            # If we found frames, include the last few
            if frame_start != -1:
                # Count frame pairs (File line + code line)
                frame_pairs = []
                i = frame_start
                while i < len(cleaned_lines):
                    if cleaned_lines[i].strip().startswith("File "):
                        # This is a frame, grab it and the next line (code)
                        frame_pairs.append(cleaned_lines[i])
                        if i + 1 < len(cleaned_lines) and not cleaned_lines[i + 1].strip().startswith("File "):
                            frame_pairs.append(cleaned_lines[i + 1])
                            i += 2
                        else:
                            i += 1
                    else:
                        # End of frames, keep the error message
                        if cleaned_lines[i].strip():
                            frame_pairs.append(cleaned_lines[i])
                        i += 1
                
                # Take last 6 lines (3 frames) plus error message
                result.extend(frame_pairs[-8:])
            else:
                # No frames found, just show what we have
                result = cleaned_lines[-6:]
                
            return result
        else:
            # In verbose mode, show all cleaned frames
            return cleaned_lines[:50]
    
    def _extract_error_chain(self, error: Any) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """Extract the chain of errors from nested exceptions."""
        chain = []
        seen = set()  # Track seen errors to avoid infinite loops
        seen_effects = {}  # Track seen effects by (tag, location) to avoid duplicates
        runtime_traceback = None  # Store the runtime traceback
        
        def extract(exc, depth=0):
            nonlocal runtime_traceback
            if depth > 10 or id(exc) in seen:  # Prevent infinite recursion
                return
            seen.add(id(exc))
            
            if isinstance(exc, EffectFailure):
                # Preserve the runtime traceback from the first EffectFailure
                if runtime_traceback is None and exc.runtime_traceback:
                    runtime_traceback = exc.runtime_traceback
                
                # Create a key for this effect based on tag and location
                if exc.creation_context:
                    effect_key = (exc.effect.__class__, exc.creation_context.format_location())
                else:
                    effect_key = (exc.effect.__class__, id(exc.effect))
                
                # Only add if we haven't seen this exact effect before
                if effect_key not in seen_effects:
                    seen_effects[effect_key] = True
                    chain.append({
                        'type': 'effect',
                        'tag': exc.effect.__class__.__name__,
                        'context': exc.creation_context,
                        'cause': exc.cause,
                        'failure': exc,
                    })
                
                # Continue with the cause to extract the full chain
                if exc.cause:
                    extract(exc.cause, depth + 1)
            elif isinstance(exc, BaseException):
                # Record regular exception only if it's not wrapped in an EffectFailure
                # Check if this is a root cause (not already captured as an effect cause)
                chain.append({
                    'type': 'exception',
                    'class': exc.__class__.__name__,
                    'message': str(exc),
                    'exc': exc
                })
                # Check for __cause__ chain
                if hasattr(exc, '__cause__') and exc.__cause__:
                    extract(exc.__cause__, depth + 1)
        
        extract(error)
        
        # Remove duplicate exceptions that are already shown as causes of effects
        seen_exceptions = set()
        filtered_chain = []
        for item in chain:
            if item['type'] == 'exception':
                exc_key = (item['class'], item['message'])
                # Check if this exception is already a cause of an effect
                is_cause = False
                for other in chain:
                    if other['type'] == 'effect' and other['cause']:
                        cause = other['cause']
                        if cause.__class__.__name__ == item['class'] and str(cause) == item['message']:
                            is_cause = True
                            break
                
                # Only add if not already shown as a cause and not a duplicate
                if not is_cause and exc_key not in seen_exceptions:
                    seen_exceptions.add(exc_key)
                    filtered_chain.append(item)
            else:
                filtered_chain.append(item)
        
        return filtered_chain, runtime_traceback
    
    def display(self, verbose: bool = False, indent: int = 2) -> str:
        """
        Display internal data in a formatted text structure.
        
        Args:
            verbose: If True, show full details including graph steps
            indent: Number of spaces for indentation
        
        Returns:
            Formatted string representation of the RunResult
        """
        lines = []
        ind = " " * indent
        
        # Header
        lines.append("=" * 60)
        lines.append("RunResult Internal Data")
        lines.append("=" * 60)
        
        # Result Status
        lines.append("\n📊 Result Status:")
        if isinstance(self.result, Ok):
            lines.append(f"{ind}✅ Success")
            value_repr = pformat(self.result.value, width=80, compact=False)
            if "\n" in value_repr:
                lines.append(f"{ind}Value:")
                lines.extend(f"{ind * 2}{line}" for line in value_repr.splitlines())
            else:
                lines.append(f"{ind}Value: {value_repr}")
        else:
            lines.append(f"{ind}❌ Failure")
            
            # Extract error chain and runtime traceback
            error_chain, runtime_traceback = self._extract_error_chain(self.result.error)
            
            if error_chain:
                # Show error chain from outermost to innermost
                lines.append(f"\n{ind}Error Chain (most recent first):")
                
                for i, error_info in enumerate(error_chain):
                    if error_info['type'] == 'effect':
                        # Show effect failure
                        lines.append(f"\n{ind}[{i+1}] Effect '{error_info['tag']}' failed")
                        
                        # Show creation location if available
                        if error_info['context']:
                            lines.append(f"{ind * 2}📍 Created at:")
                            location = error_info['context'].format_location()
                            lines.append(f"{ind * 3}{location}")
                            if error_info['context'].code:
                                lines.append(f"{ind * 3}{error_info['context'].code}")
                            creation_trace = self._format_creation_trace(
                                error_info['context'], indent, max_frames=18
                            )
                            if creation_trace:
                                lines.append(f"{ind * 2}🧵 Creation stack:")
                                for frame_line in creation_trace:
                                    lines.append(f"{ind * 3}{frame_line}")

                        # Show the immediate cause if it's not another effect
                        if error_info['cause'] and not isinstance(error_info['cause'], EffectFailure):
                            cause = error_info['cause']
                            lines.append(f"{ind * 2}Caused by: {cause.__class__.__name__}: {cause}")
                            cause_trace = self._format_exception_trace(cause, max_frames=18)
                            if cause_trace:
                                lines.append(f"{ind * 2}🔥 Cause stack:")
                                for frame_line in cause_trace:
                                    lines.append(f"{ind * 3}{frame_line}")
                        failure = error_info.get('failure')
                        if failure and failure.runtime_traceback:
                            lines.append(f"{ind * 2}🔥 Effect runtime stack:")
                            for tb_line in self._format_execution_traceback(
                                failure.runtime_traceback, verbose
                            ):
                                if tb_line.strip():
                                    lines.append(f"{ind * 3}{tb_line}")

                    elif error_info['type'] == 'exception':
                        # Show regular exception
                        lines.append(f"\n{ind}[{i+1}] {error_info['class']}: {error_info['message']}")
                
                # Always show execution stack trace if available (critical debugging info)
                if runtime_traceback:
                    lines.append(f"\n{ind}🔥 Execution Stack Trace (where error occurred):")
                    # Use the dedicated formatter
                    formatted_tb = self._format_execution_traceback(runtime_traceback, verbose)
                    for tb_line in formatted_tb:
                        if tb_line.strip():
                            lines.append(f"{ind * 2}{tb_line}")
                
                # Show detailed creation stack traces if verbose
                if verbose:
                    
                    # Show creation stack trace if available
                    if error_chain:
                        for error_info in error_chain:
                            if error_info['type'] == 'effect' and error_info['context'] and error_info['context'].stack_trace:
                                lines.append(f"\n{ind}📍 Effect Creation Stack Trace (where effect was created):")
                                traceback_lines = error_info['context'].build_traceback().split("\n")
                                for tb_line in traceback_lines[:20]:  # Limit lines
                                    lines.append(f"{ind * 2}{tb_line}")
                                break
            else:
                # No error chain extracted, show simple error info
                lines.append(f"{ind}Exception Type: {self.result.error.__class__.__name__}")
                lines.append(f"{ind}Exception Message: {self.result.error}")
                # Always try to show execution trace
                if runtime_traceback:
                    lines.append(f"\n{ind}🔥 Execution Stack Trace:")
                    formatted_tb = self._format_execution_traceback(runtime_traceback, verbose)
                    for tb_line in formatted_tb:
                        if tb_line.strip():
                            lines.append(f"{ind * 2}{tb_line}")
                elif verbose and hasattr(self.result.error, "__traceback__") and self.result.error.__traceback__:
                        lines.append(f"\n{ind}📍 Stack Trace:")
                        tb_lines = traceback.format_exception(
                            type(self.result.error), self.result.error, self.result.error.__traceback__
                        )
                        for tb_line in "".join(tb_lines).split("\n"):
                            if tb_line.strip():
                                lines.append(f"{ind * 2}{tb_line}")
        
        # State
        lines.append("\n🗂️ State:")
        if self.state:
            for key, value in list(self.state.items())[:20]:  # Limit items shown
                value_str = self._format_value(value, indent, max_length=100)
                lines.append(f"{ind}{key}: {value_str}")
            if len(self.state) > 20:
                lines.append(f"{ind}... and {len(self.state) - 20} more items")
        else:
            lines.append(f"{ind}(empty)")
        
        # Logs
        lines.append("\n📝 Logs:")
        if self.log:
            for i, entry in enumerate(self.log[:10]):  # Show first 10 logs
                entry_str = self._format_value(entry, indent, max_length=150)
                lines.append(f"{ind}[{i}] {entry_str}")
            if len(self.log) > 10:
                lines.append(f"{ind}... and {len(self.log) - 10} more entries")
        else:
            lines.append(f"{ind}(no logs)")

        # Dep/Ask effect usage
        lines.append("\n🔗 Dep/Ask Usage:")
        dep_ask_observations = [
            obs for obs in self.effect_observations if obs.effect_type in {"Dep", "Ask"}
        ]
        if dep_ask_observations:
            limit = 40
            for idx, obs in enumerate(dep_ask_observations[:limit], start=1):
                key_text = f" key={obs.key!r}" if obs.key is not None else ""
                lines.append(f"{ind}[{idx}] {obs.effect_type}{key_text}")
                if obs.context is not None:
                    lines.append(f"{ind * 2}{obs.context.format_location()}")
                    if obs.context.code:
                        lines.append(f"{ind * 3}{obs.context.code}")
                else:
                    lines.append(f"{ind * 2}<location unavailable>")
            if len(dep_ask_observations) > limit:
                remaining = len(dep_ask_observations) - limit
                lines.append(f"{ind}... and {remaining} more entries")
        else:
            lines.append(f"{ind}(no Dep/Ask effects observed)")

        # Graph
        lines.append("\n🌳 Graph:")
        if self.graph and self.graph.steps:
            lines.append(f"{ind}Steps: {len(self.graph.steps)}")
            if verbose:
                # Show graph steps in verbose mode
                for i, step in enumerate(list(self.graph.steps)[:5]):
                    lines.append(f"{ind}Step {i}:")
                    if step.meta:
                        lines.append(f"{ind * 2}Meta: {self._format_value(step.meta, indent * 2, max_length=100)}")
                    lines.append(f"{ind * 2}Inputs: {len(step.inputs)} nodes")
                    lines.append(f"{ind * 2}Output: {step.output.value.__class__.__name__ if step.output.value else 'None'}")
                if len(self.graph.steps) > 5:
                    lines.append(f"{ind}... and {len(self.graph.steps) - 5} more steps")
        else:
            lines.append(f"{ind}(no graph steps)")
        
        # Environment (in verbose mode)
        if verbose and self.env:
            lines.append("\n🌍 Environment:")
            for key, value in list(self.env.items())[:10]:
                value_str = self._format_value(value, indent, max_length=100)
                lines.append(f"{ind}{key}: {value_str}")
            if len(self.env) > 10:
                lines.append(f"{ind}... and {len(self.env) - 10} more items")
        
        # Summary
        lines.append("\n" + "=" * 60)
        lines.append("Summary:")
        lines.append(f"  • Status: {'✅ OK' if self.is_ok else '❌ Error'}")
        lines.append(f"  • State items: {len(self.state)}")
        lines.append(f"  • Log entries: {len(self.log)}")
        lines.append(f"  • Graph steps: {len(self.graph.steps) if self.graph else 0}")
        lines.append(f"  • Environment vars: {len(self.env)}")
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
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

    def _format_creation_trace(
        self,
        context: EffectCreationContext,
        indent: int,
        max_frames: int = 15,
    ) -> list[str]:
        """Format the creation stack trace for display."""

        if not context.stack_trace:
            return []

        trace_lines: list[str] = []
        for frame in context.stack_trace[:max_frames]:
            filename = frame.get("filename", "<unknown>")
            line = frame.get("line", "?")
            func = frame.get("function", "<unknown>")
            trace_lines.append(f"File \"{filename}\", line {line}, in {func}")
            code = frame.get("code")
            if code:
                trace_lines.append(f"  {code}")

        # Ensure the immediate creation site is always included last
        creation_site = f'File "{context.filename}", line {context.line}, in {context.function}'
        if creation_site not in trace_lines:
            trace_lines.append(creation_site)
            if context.code:
                trace_lines.append(f"  {context.code}")

        return trace_lines

    def _format_exception_trace(
        self, exc: BaseException, max_frames: int = 18
    ) -> list[str]:
        tb = exc.__traceback__
        if tb is None:
            return []

        formatted = traceback.format_exception(type(exc), exc, tb)
        if not formatted:
            return []

        lines = []
        for raw in formatted:
            for piece in raw.rstrip().split("\n"):
                if piece.strip():
                    lines.append(piece)

        if max_frames is not None and max_frames > 0 and len(lines) > max_frames:
            lines = lines[-max_frames:]

        trailer = f"{type(exc).__name__}: {exc}"
        if lines and lines[-1].strip() == trailer:
            lines = lines[:-1]

        return lines


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

    if isinstance(value, Program):
        return value.intercept(transform)

    if isinstance(value, Effect):
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
