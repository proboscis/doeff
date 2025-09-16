"""
Core types for the doeff effects system.

This module contains the foundational types with zero internal dependencies.
"""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional, TypeVar, Union, TYPE_CHECKING

# Import Program for type alias, but avoid circular imports
if TYPE_CHECKING:
    from doeff.program import Program

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


# ============================================
# Effect Failure Exception
# ============================================

class EffectFailure(RuntimeError):
    """Exception raised when an effect fails, carrying creation context."""
    
    def __init__(self, effect_tag: str, creation_context: Optional[EffectCreationContext], cause: Exception):
        self.effect_tag = effect_tag
        self.creation_context = creation_context
        self.cause = cause
        
        # Build the error message
        msg_parts = [f"Effect '{effect_tag}' failed"]
        if creation_context:
            msg_parts.append(f"\n📍 Creation Location:\n{creation_context.format_full()}")
        msg_parts.append(f"\n❌ Error: {cause}")
        
        super().__init__("\n".join(msg_parts))
        self.__cause__ = cause


# ============================================
# Core Effect Type
# ============================================

@dataclass(frozen=True)
class Effect:
    """Effect with tag and payload.

    This single type represents ALL effects in our system. We use string tags
    instead of separate types because Python lacks proper sum types/GADTs.
    The trade-off is runtime type checking vs compile-time safety.
    """

    tag: str  # String discrimination instead of type-based
    payload: Any  # Untyped payload - Python can't express effect-specific types
    created_at: Optional[EffectCreationContext] = None  # Optional creation context for debugging


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
    # Cache storage (shared across parallel executions)
    cache: Dict[str, Any] = field(default_factory=dict)

    def copy(self) -> ExecutionContext:
        """Create a shallow copy of the context."""
        return ExecutionContext(
            env=self.env.copy(),
            state=self.state.copy(),
            log=self.log.copy(),
            graph=self.graph,
            io_allowed=self.io_allowed,
            cache=self.cache,  # Cache is shared reference, not copied
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
        )


# ============================================
# Run Result
# ============================================

@dataclass(frozen=True)
class RunResult[T]:
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
            lines.append(f"{ind}Value: {self._format_value(self.result.value, indent)}")
        else:
            lines.append(f"{ind}❌ Failure")
            
            # Extract the actual error details
            error = self.result.error
            if isinstance(error, TraceError):
                # Get the actual exception
                actual_exc = error.exc
                
                # Check if this is an EffectFailure exception
                if isinstance(actual_exc, EffectFailure):
                    # Show which effect failed
                    lines.append(f"{ind}Failed Effect: '{actual_exc.effect_tag}'")
                    
                    # Show the original exception that caused the failure
                    lines.append(f"\n{ind}❌ Execution Error:")
                    lines.append(f"{ind * 2}Type: {actual_exc.cause.__class__.__name__}")
                    lines.append(f"{ind * 2}Message: {actual_exc.cause}")
                    
                    # Show where the effect was created
                    if actual_exc.creation_context:
                        lines.append(f"\n{ind}📍 Effect Creation Stack Trace:")
                        # Use the build_traceback method for proper stack trace format
                        traceback_lines = actual_exc.creation_context.build_traceback().split("\n")
                        for tb_line in traceback_lines:
                            lines.append(f"{ind * 2}{tb_line}")
                
                # Check if this is a wrapped RuntimeError with effect creation context (legacy)
                elif isinstance(actual_exc, RuntimeError) and "Effect created at" in str(actual_exc):
                    # Legacy handling for old-style wrapped errors
                    error_msg = str(actual_exc)
                    parts = error_msg.split("\n\n")
                    
                    # Show the effect failure info
                    for part in parts:
                        if part.startswith("Effect "):
                            lines.append(f"{ind}Error: {part}")
                        elif "📍" in part or "Effect created at" in part:
                            # Show creation context
                            lines.append(f"\n{ind}📍 Effect Creation Context:")
                            for line in part.split("\n"):
                                if line.strip() and not line.startswith("📍"):
                                    lines.append(f"{ind * 2}{line.strip()}")
                        elif "❌" in part:
                            # Show the actual error that occurred
                            lines.append(f"\n{ind}❌ Execution Error:")
                            error_part = part.replace("❌ Error: ", "")
                            lines.append(f"{ind * 2}{error_part}")
                    
                    # Show the original cause if available
                    if actual_exc.__cause__:
                        lines.append(f"\n{ind}Original Exception:")
                        lines.append(f"{ind * 2}Type: {actual_exc.__cause__.__class__.__name__}")
                        lines.append(f"{ind * 2}Message: {actual_exc.__cause__}")
                else:
                    # Regular TraceError without effect context
                    lines.append(f"{ind}Exception Type: {actual_exc.__class__.__name__}")
                    lines.append(f"{ind}Exception Message: {actual_exc}")
                
                # Show the full traceback
                lines.append(f"\n{ind}📍 Stack Trace:")
                if error.tb:
                    tb_lines = error.tb.strip().split("\n")
                    # Always show the full traceback for errors (not just in verbose mode)
                    for tb_line in tb_lines:
                        if tb_line.strip():  # Skip empty lines
                            lines.append(f"{ind * 2}{tb_line}")
            else:
                # Non-TraceError error
                lines.append(f"{ind}Error Type: {error.__class__.__name__}")
                lines.append(f"{ind}Error: {error}")
                if hasattr(error, "__traceback__") and error.__traceback__ and verbose:
                    lines.append(f"\n{ind}📍 Stack Trace:")
                    tb_lines = traceback.format_exception(
                        type(error), error, error.__traceback__
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
    "RunResult",
    "ListenResult",
]