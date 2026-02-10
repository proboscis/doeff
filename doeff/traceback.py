"""
Abstract traceback protocol for interpreter-agnostic error handling.

This module defines the EffectTraceback protocol that both CESK and ProgramInterpreter
implementations use to provide rich error context. Users can work with tracebacks
without knowing which interpreter executed their program.

Usage:
    from doeff import Safe, do
    from doeff import Err, Some

    @do
    def main():
        result = yield Safe(risky_operation())

        match result:
            case Err(error=ex, captured_traceback=Some(trace)):
                print(trace.format())        # Full traceback
                print(trace.format_short())  # One-liner
                return f"Failed with trace"
            case Err(error=ex):
                return f"Failed: {ex}"

Public API:
    - EffectTraceback: Protocol for traceback implementations
    - PythonTraceback: Basic traceback wrapping exception.__traceback__
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from types import TracebackType


@runtime_checkable
class EffectTraceback(Protocol):
    """Abstract traceback protocol - both interpreters implement this.

    This protocol provides a consistent interface for traceback information
    regardless of which interpreter executed the program:
    - CESK: CapturedTraceback (rich - effect chain + Python chain)
    - ProgramInterpreter: PythonTraceback (basic - wraps exception.__traceback__)
    """

    def format(self) -> str:
        """Full human-readable traceback.

        Returns:
            Multi-line string similar to Python's standard traceback format.
        """
        ...

    def format_short(self) -> str:
        """One-line summary.

        Returns:
            Single line summary like: "func1 -> func2 -> func3: ExceptionType: message"
        """
        ...

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable representation.

        Returns:
            Dict suitable for logging, transport, or serialization.
        """
        ...


@dataclass(frozen=True)
class PythonTraceback:
    """Basic traceback implementation wrapping Python's exception traceback.

    This class implements the EffectTraceback protocol for use with
    ProgramInterpreter. It wraps a standard Python traceback and provides
    formatted output similar to Python's traceback module.

    Attributes:
        exception: The exception whose traceback is wrapped
        traceback_obj: The traceback object (exception.__traceback__)
        capture_timestamp: time.time() when captured (optional)
    """

    exception: BaseException
    traceback_obj: TracebackType | None = None
    capture_timestamp: float | None = field(default=None)

    def __post_init__(self) -> None:
        if self.traceback_obj is None and self.exception.__traceback__ is not None:
            object.__setattr__(self, "traceback_obj", self.exception.__traceback__)
        if self.capture_timestamp is None:
            object.__setattr__(self, "capture_timestamp", time.time())

    def format(self) -> str:
        """Full human-readable traceback.

        Returns:
            Multi-line string in standard Python traceback format.
        """
        lines: list[str] = []

        lines.append("Python Traceback (most recent call last):")

        if self.traceback_obj is not None:
            tb_lines = traceback.format_tb(self.traceback_obj)
            for tb_line in tb_lines:
                for line in tb_line.rstrip("\n").split("\n"):
                    if line:
                        lines.append(f"  {line.lstrip()}" if not line.startswith("  ") else line)
        else:
            lines.append("  (no traceback available)")

        lines.append("")

        exc_type = type(self.exception).__name__
        exc_msg = str(self.exception)
        lines.append(f"{exc_type}: {exc_msg}")

        return "\n".join(lines)

    def format_short(self) -> str:
        """One-line summary of the traceback.

        Format: <location>: ExceptionType: message

        Returns:
            Single line summary.
        """
        exc_type = type(self.exception).__name__
        exc_msg = str(self.exception).replace("\n", "\\n")

        location = "<unknown>"
        if self.traceback_obj is not None:
            tb = self.traceback_obj
            while tb.tb_next is not None:
                tb = tb.tb_next
            frame = tb.tb_frame
            func_name = frame.f_code.co_name
            location = func_name

        return f"{location}: {exc_type}: {exc_msg}"

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict.

        Schema:
            {
                "version": "1.0",
                "type": "python",
                "frames": [...],
                "exception": {...},
                "metadata": {...}
            }

        Returns:
            JSON-serializable dict.
        """
        frames: list[dict[str, Any]] = []

        if self.traceback_obj is not None:
            tb = self.traceback_obj
            while tb is not None:
                frame = tb.tb_frame
                frames.append(
                    {
                        "filename": frame.f_code.co_filename,
                        "lineno": tb.tb_lineno,
                        "function": frame.f_code.co_name,
                        "code": None,
                    }
                )
                tb = tb.tb_next

        return {
            "version": "1.0",
            "type": "python",
            "frames": frames,
            "exception": {
                "type": type(self.exception).__name__,
                "qualified_type": f"{type(self.exception).__module__}.{type(self.exception).__name__}",
                "message": str(self.exception),
                "args": list(self.exception.args) if self.exception.args else [],
            },
            "metadata": {
                "capture_timestamp": self.capture_timestamp,
                "interpreter": "program_interpreter",
            },
        }


def capture_python_traceback(ex: BaseException) -> PythonTraceback:
    """Capture a PythonTraceback from an exception.

    Convenience function for creating a PythonTraceback with proper
    timestamp and traceback capture.

    Args:
        ex: The exception to capture traceback from

    Returns:
        PythonTraceback instance
    """
    return PythonTraceback(
        exception=ex,
        traceback_obj=ex.__traceback__,
        capture_timestamp=time.time(),
    )


__all__ = [
    "EffectTraceback",
    "PythonTraceback",
    "capture_python_traceback",
]
