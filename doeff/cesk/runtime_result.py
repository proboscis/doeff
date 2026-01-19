"""RuntimeResult protocol and implementation per SPEC-CESK-002.

This module provides the RuntimeResult protocol - the standard return type
from runtime execution. It wraps the computation result with full debugging
context including three complementary stack trace views.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar, runtime_checkable

from doeff._vendor import Err, Ok, Result

if TYPE_CHECKING:
    from doeff.cesk_traceback import (
        CapturedTraceback,
        EffectFrame as CTEffectFrame,
        PythonFrame as CTPythonFrame,
    )

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


# ============================================
# Stack Trace Types
# ============================================

@dataclass(frozen=True)
class SourceLocation:
    """Source code location."""
    filename: str
    line: int
    function: str
    code_context: str | None = None


@dataclass(frozen=True)
class KFrame:
    """Single frame in the continuation stack."""
    frame_type: str          # "SafeFrame", "LocalFrame", etc.
    description: str         # Human-readable details
    source_location: SourceLocation | None = None


@dataclass(frozen=True)
class KStackTrace:
    """CESK continuation stack snapshot."""
    frames: tuple[KFrame, ...]

    def format(self) -> str:
        """Format as readable stack."""
        if not self.frames:
            return "Continuation Stack (K): <empty>"

        lines = ["Continuation Stack (K):"]
        for i, frame in enumerate(self.frames):
            lines.append(f"  [{i}] {frame.frame_type:18} - {frame.description}")
        return "\n".join(lines)


@dataclass(frozen=True)
class EffectCallNode:
    """Node in the effect call tree."""
    name: str                           # Function name or effect name
    is_effect: bool                     # True if this is a leaf effect
    args_repr: str = ""                 # Short repr of arguments
    count: int = 1                      # How many times (for effects)
    children: tuple["EffectCallNode", ...] = ()
    source_location: SourceLocation | None = None
    is_error_site: bool = False         # True if error occurred here


@dataclass(frozen=True)
class EffectStackTrace:
    """Hierarchical view of effects grouped by program call stack."""
    root: EffectCallNode | None = None

    def format(self) -> str:
        """Format as ASCII tree."""
        if self.root is None:
            return "Effect Call Tree: <not captured>"

        lines = ["Effect Call Tree:"]
        self._format_node(self.root, lines, prefix="  ", is_last=True)
        return "\n".join(lines)

    def _format_node(
        self,
        node: EffectCallNode,
        lines: list[str],
        prefix: str,
        is_last: bool,
    ) -> None:
        connector = "└─ " if is_last else "├─ "
        suffix = ""
        if node.count > 1:
            suffix = f" x{node.count}"
        if node.is_error_site:
            suffix += "  <-- ERROR"

        display = node.name
        if node.args_repr:
            display = f"{node.name}({node.args_repr})"

        lines.append(f"{prefix}{connector}{display}{suffix}")

        child_prefix = prefix + ("   " if is_last else "│  ")
        for i, child in enumerate(node.children):
            self._format_node(child, lines, child_prefix, i == len(node.children) - 1)

    def get_effect_path(self) -> str:
        """Get linear effect path string: main() -> fetch() -> Ask('key')."""
        if self.root is None:
            return "<not captured>"
        
        path_parts: list[str] = []
        self._collect_path(self.root, path_parts)
        return " -> ".join(path_parts) if path_parts else "<empty>"
    
    def _collect_path(self, node: EffectCallNode, path: list[str]) -> None:
        """Collect path to deepest error or leaf."""
        display = node.name
        if node.args_repr:
            display = f"{node.name}({node.args_repr})"
        path.append(display)
        
        # Find error child or last child
        error_child = None
        for child in node.children:
            if child.is_error_site:
                error_child = child
                break
        
        if error_child:
            self._collect_path(error_child, path)
        elif node.children:
            # No error child, go to last child (deepest path)
            self._collect_path(node.children[-1], path)


@dataclass(frozen=True)
class PythonFrame:
    """Single Python stack frame."""
    filename: str
    line: int
    function: str
    code_context: str | None = None


@dataclass(frozen=True)
class PythonStackTrace:
    """Python source locations for effect creation."""
    frames: tuple[PythonFrame, ...]

    def format(self) -> str:
        """Format as Python traceback."""
        if not self.frames:
            return "Python Stack: <not captured>"

        lines = ["Python Stack:"]
        for frame in self.frames:
            lines.append(f'  File "{frame.filename}", line {frame.line}, in {frame.function}')
            if frame.code_context:
                lines.append(f"    {frame.code_context.strip()}")
        return "\n".join(lines)


# ============================================
# RuntimeResult Protocol
# ============================================

@runtime_checkable
class RuntimeResult(Protocol[T_co]):
    """Protocol for runtime execution results.

    Wraps the computation outcome with execution context and debugging info.
    All runtimes MUST return an object satisfying this protocol.
    """

    @property
    def result(self) -> Result[T_co]:
        """The computation outcome: Ok(value) or Err(error)."""
        ...

    @property
    def state(self) -> dict[str, Any]:
        """Final state after execution (from Put/Modify effects)."""
        ...

    @property
    def log(self) -> list[Any]:
        """Accumulated messages from Tell effects."""
        ...

    @property
    def k_stack(self) -> KStackTrace:
        """CESK continuation stack at termination point."""
        ...

    @property
    def effect_stack(self) -> EffectStackTrace:
        """Effect call tree showing which @do functions called which effects."""
        ...

    @property
    def python_stack(self) -> PythonStackTrace:
        """Python source locations where effects were created."""
        ...

    @property
    def graph(self) -> Any | None:
        """Computation graph if capture was enabled, else None."""
        ...

    @property
    def env(self) -> dict[Any, Any]:
        """Final environment."""
        ...

    @property
    def value(self) -> T_co:
        """Unwrap Ok value or raise the Err error."""
        ...

    @property
    def error(self) -> BaseException:
        """Get the error if result is Err, else raise ValueError."""
        ...

    def is_ok(self) -> bool:
        """True if result is Ok."""
        ...

    def is_err(self) -> bool:
        """True if result is Err."""
        ...

    def format(self, *, verbose: bool = False) -> str:
        """Format the result for display with all stack traces."""
        ...


# ============================================
# Conversion Helpers
# ============================================

def _convert_python_frames(ct_frames: tuple["CTPythonFrame", ...]) -> tuple[PythonFrame, ...]:
    """Convert CapturedTraceback PythonFrames to RuntimeResult PythonFrames."""
    result = []
    for f in ct_frames:
        result.append(PythonFrame(
            filename=f.location.filename,
            line=f.location.lineno,
            function=f.location.function,
            code_context=f.location.code,
        ))
    return tuple(result)


def _convert_effect_frames_to_tree(
    ct_frames: tuple["CTEffectFrame", ...],
    error_function: str | None = None,
) -> EffectStackTrace:
    """Convert CapturedTraceback EffectFrames to EffectStackTrace tree.
    
    Creates a linear chain from effect frames (outermost to innermost).
    """
    if not ct_frames:
        return EffectStackTrace(root=None)
    
    # Build from innermost to outermost (reverse order)
    # ct_frames is in outermost→innermost order, we reverse to build bottom-up
    reversed_frames = list(reversed(ct_frames))
    
    current_node: EffectCallNode | None = None
    for i, ef in enumerate(reversed_frames):
        is_innermost = i == 0
        is_error = bool(is_innermost and error_function and ef.location.function == error_function)
        
        node = EffectCallNode(
            name=ef.location.function,
            is_effect=is_innermost,  # Innermost is typically the effect
            args_repr="",
            count=1,
            children=(current_node,) if current_node else (),
            source_location=SourceLocation(
                filename=ef.location.filename,
                line=ef.location.lineno,
                function=ef.location.function,
                code_context=ef.location.code,
            ),
            is_error_site=is_error,
        )
        current_node = node
    
    return EffectStackTrace(root=current_node)


def build_stacks_from_captured_traceback(
    captured_tb: "CapturedTraceback | None",
) -> tuple[PythonStackTrace, EffectStackTrace]:
    """Build PythonStackTrace and EffectStackTrace from CapturedTraceback."""
    if captured_tb is None:
        return PythonStackTrace(frames=()), EffectStackTrace(root=None)
    
    python_stack = PythonStackTrace(frames=_convert_python_frames(captured_tb.python_frames))
    
    # Get error function name from innermost effect frame
    error_func = None
    if captured_tb.effect_frames:
        error_func = captured_tb.effect_frames[-1].location.function
    
    effect_stack = _convert_effect_frames_to_tree(captured_tb.effect_frames, error_func)
    
    return python_stack, effect_stack


# ============================================
# Concrete Implementation
# ============================================

@dataclass
class RuntimeResultImpl(Generic[T]):
    """Concrete implementation of RuntimeResult protocol."""

    _result: Result[T]
    _state: dict[str, Any] = field(default_factory=dict)
    _log: list[Any] = field(default_factory=list)
    _env: dict[Any, Any] = field(default_factory=dict)
    _k_stack: KStackTrace = field(default_factory=lambda: KStackTrace(frames=()))
    _effect_stack: EffectStackTrace = field(default_factory=EffectStackTrace)
    _python_stack: PythonStackTrace = field(default_factory=lambda: PythonStackTrace(frames=()))
    _graph: Any | None = None
    _captured_traceback: Any | None = None  # CapturedTraceback

    @property
    def result(self) -> Result[T]:
        return self._result

    @property
    def state(self) -> dict[str, Any]:
        return self._state

    @property
    def log(self) -> list[Any]:
        return self._log

    @property
    def env(self) -> dict[Any, Any]:
        return self._env

    @property
    def k_stack(self) -> KStackTrace:
        return self._k_stack

    @property
    def effect_stack(self) -> EffectStackTrace:
        return self._effect_stack

    @property
    def python_stack(self) -> PythonStackTrace:
        return self._python_stack

    @property
    def graph(self) -> Any | None:
        return self._graph

    @property
    def value(self) -> T:
        """Unwrap Ok value or raise the Err error."""
        if isinstance(self._result, Ok):
            return self._result.ok()  # type: ignore[return-value]
        else:
            raise self._result.err()  # type: ignore[misc]

    @property
    def error(self) -> BaseException:
        """Get the error if result is Err, else raise ValueError."""
        if isinstance(self._result, Err):
            return self._result.error
        raise ValueError("Result is Ok, no error")

    def is_ok(self) -> bool:
        return isinstance(self._result, Ok)

    def is_err(self) -> bool:
        return isinstance(self._result, Err)

    def format(self, *, verbose: bool = False) -> str:
        """Format the result for display with all stack traces."""
        if verbose:
            return self._format_verbose()
        return self._format_condensed()

    def _format_condensed(self) -> str:
        """Condensed format for quick debugging."""
        lines = []

        # Status line
        if self.is_ok():
            lines.append(f"Ok({self._result.ok()!r})")
        else:
            err = self._result.err()  # type: ignore[union-attr]
            lines.append(f"Err({type(err).__name__}: {err})")
            lines.append("")
            lines.append(f"Root Cause: {type(err).__name__}: {err}")

            # Show just the last few Python frames
            if self._python_stack.frames:
                lines.append("")
                for frame in self._python_stack.frames[-3:]:
                    lines.append(f'  File "{frame.filename}", line {frame.line}, in {frame.function}')
                    if frame.code_context:
                        lines.append(f"    {frame.code_context.strip()}")

            # Effect path (REQUIRED by spec)
            lines.append("")
            lines.append(f"Effect path: {self._effect_stack.get_effect_path()}")

        # K stack summary
        if self._k_stack.frames:
            frame_types = [f.frame_type for f in self._k_stack.frames]
            lines.append("")
            lines.append(f"K: [{', '.join(frame_types)}]")

        return "\n".join(lines)

    def _format_verbose(self) -> str:
        """Full verbose format with all debugging info."""
        sep = "═" * 79
        thin_sep = "─" * 79

        lines = [
            sep,
            "                              RUNTIME RESULT",
            sep,
            "",
        ]

        # Status
        if self.is_ok():
            lines.append(f"Status: Ok({self._result.ok()!r})")
        else:
            err = self._result.err()  # type: ignore[union-attr]
            lines.append(f"Status: Err({type(err).__name__}: {err})")
            lines.append("")
            lines.append(thin_sep)
            lines.append("                               ROOT CAUSE")
            lines.append(thin_sep)
            lines.append(f"{type(err).__name__}: {err}")

        # Python Stack
        lines.append("")
        lines.append(thin_sep)
        lines.append("                             PYTHON STACK")
        lines.append(thin_sep)
        lines.append(self._python_stack.format())

        # Effect Call Tree
        lines.append("")
        lines.append(thin_sep)
        lines.append("                           EFFECT CALL TREE")
        lines.append(thin_sep)
        lines.append(self._effect_stack.format())

        # K Stack
        lines.append("")
        lines.append(thin_sep)
        lines.append("                         CONTINUATION STACK (K)")
        lines.append(thin_sep)
        lines.append(self._k_stack.format())

        # State & Log
        lines.append("")
        lines.append(thin_sep)
        lines.append("                              STATE & LOG")
        lines.append(thin_sep)
        lines.append("State:")
        if self._state:
            for k, v in list(self._state.items())[:10]:
                if not k.startswith("__"):  # Skip internal keys
                    lines.append(f"  {k}: {v!r}")
        else:
            lines.append("  <empty>")

        lines.append("")
        lines.append("Log:")
        if self._log:
            for i, entry in enumerate(self._log[:10]):
                lines.append(f"  [{i}] {entry!r}")
            if len(self._log) > 10:
                lines.append(f"  ... and {len(self._log) - 10} more")
        else:
            lines.append("  <empty>")

        lines.append("")
        lines.append(sep)

        return "\n".join(lines)


def build_k_stack_trace(kontinuation: list[Any]) -> KStackTrace:
    """Build KStackTrace from a continuation stack."""
    frames = []
    for frame in kontinuation:
        frame_type = type(frame).__name__
        description = _describe_frame(frame)
        frames.append(KFrame(frame_type=frame_type, description=description))
    return KStackTrace(frames=tuple(frames))


def _describe_frame(frame: Any) -> str:
    """Generate human-readable description for a frame."""
    frame_type = type(frame).__name__

    if frame_type == "SafeFrame":
        return "will catch errors"
    elif frame_type == "LocalFrame":
        env = getattr(frame, "restore_env", {})
        if env:
            keys = list(env.keys())[:3]
            return f"env={{{', '.join(repr(k) for k in keys)}{'...' if len(env) > 3 else ''}}}"
        return "env={}"
    elif frame_type == "InterceptFrame":
        transforms = getattr(frame, "transforms", ())
        return f"{len(transforms)} transform(s)"
    elif frame_type == "GatherFrame":
        remaining = getattr(frame, "remaining_programs", [])
        collected = getattr(frame, "collected_results", [])
        total = len(remaining) + len(collected) + 1
        return f"completed {len(collected)}/{total} children"
    elif frame_type == "ListenFrame":
        return "capturing logs"
    elif frame_type == "ReturnFrame":
        return "resume generator"
    elif frame_type == "RaceFrame":
        task_ids = getattr(frame, "task_ids", ())
        return f"{len(task_ids)} racing tasks"
    elif frame_type == "GraphCaptureFrame":
        return "capturing graph"
    else:
        return ""


__all__ = [
    # Stack trace types
    "SourceLocation",
    "KFrame",
    "KStackTrace",
    "EffectCallNode",
    "EffectStackTrace",
    "PythonFrame",
    "PythonStackTrace",
    # Protocol and implementation
    "RuntimeResult",
    "RuntimeResultImpl",
    # Helpers
    "build_k_stack_trace",
    "build_stacks_from_captured_traceback",
]
