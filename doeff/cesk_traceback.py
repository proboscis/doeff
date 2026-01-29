"""
CESK Interpreter Effect Traceback Capture.

This module provides data structures and functions for capturing and formatting
traceback information from the CESK interpreter. When an exception occurs during
interpretation, users can see both:

1. The Effect/Kleisli call chain: Which @do functions were called and where each yield happened
2. The Python call chain: The pure function calls that led to the actual raise

Usage:
    from doeff.cesk import run_sync, CESKResult
    from doeff.cesk_traceback import format_traceback

    result = run_sync(my_program())

    if result.is_err():
        print(result.error)  # Original exception, untouched
        if result.captured_traceback:
            print(format_traceback(result.captured_traceback))

Public API:
    - CapturedTraceback: Complete captured traceback data
    - EffectFrame: Single frame from effect/Kleisli call chain
    - PythonFrame: Single frame from Python's traceback
    - CodeLocation: A specific location in source code
    - format_traceback(): Human-readable traceback string
    - format_traceback_short(): One-line summary
    - to_dict(): JSON-serializable dict for logging/transport
"""

from __future__ import annotations

import linecache
import sys
import time
import warnings
from collections.abc import Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from doeff.cesk import Kontinuation
    from doeff.program import KleisliProgramCall


# ============================================================================
# Public Data Types
# ============================================================================


@dataclass(frozen=True)
class CodeLocation:
    """
    A specific location in source code.

    Reusable across EffectFrame, PythonFrame, and call-site references.
    """

    filename: str
    lineno: int
    function: str
    code: str | None = None  # Source line at this location


@dataclass(frozen=True)
class EffectFrame:
    """
    A single frame from the effect/Kleisli call chain.

    Captured from ReturnFrame.generator.gi_frame while generator is alive.

    Attributes:
        location: Where this generator is currently paused
        frame_kind: What kind of K frame this came from
            - "kleisli_entry": First execution (generator not yet started)
            - "kleisli_yield": Paused at yield
            - "kleisli_closed": Edge case: generator closed before capture
        call_site: Where this generator was yielded from (parent's yield line).
            Available for nested calls, None for top-level entry point.
        locals_snapshot: Optional generator locals at capture time (for debugging).
            Sanitized repr strings, not live objects. Only captured if explicitly
            requested (privacy/size concerns). Always None in v1.
    """

    location: CodeLocation
    frame_kind: str
    call_site: CodeLocation | None = None
    locals_snapshot: dict[str, str] | None = None


@dataclass(frozen=True)
class PythonFrame:
    """
    A single frame from Python's exception traceback.

    Captured from exception.__traceback__ chain.

    Attributes:
        location: Source location
        locals_snapshot: Optional frame locals at exception time.
            Sanitized repr strings, not live objects. Always None in v1.
    """

    location: CodeLocation
    locals_snapshot: dict[str, str] | None = None


@dataclass(frozen=True)
class CapturedTraceback:
    """
    Complete captured traceback from a CESK interpreter error.

    Contains both the effect chain (from K stack) and Python chain
    (from exception traceback) as separate data for maximum flexibility.

    Attributes:
        effect_frames: Effect/Kleisli call chain (outermost to innermost).
            Captured from K stack's ReturnFrame generators.
        python_frames: Python call chain (outermost to innermost, standard traceback order).
            Captured from exception.__traceback__.
        exception_type: Exception class name (e.g., "RuntimeError")
        exception_message: String representation of exception
        exception_args: Original exception args tuple
        exception: The actual exception object (for re-raising, chaining, etc.)
            Note: May hold references to frames; consider memory implications.
        capture_timestamp: time.time() when captured
        interpreter_version: Version identifier for the CESK interpreter
    """

    effect_frames: tuple[EffectFrame, ...]
    python_frames: tuple[PythonFrame, ...]
    exception_type: str
    exception_message: str
    exception_args: tuple[Any, ...]
    exception: BaseException
    capture_timestamp: float | None = None
    interpreter_version: str = "cesk-v1"

    def format(self) -> str:
        """Full human-readable traceback (implements EffectTraceback protocol)."""
        return format_traceback(self)

    def format_short(self) -> str:
        """One-line summary (implements EffectTraceback protocol)."""
        return format_traceback_short(self)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable representation (implements EffectTraceback protocol)."""
        result = to_dict(self)
        return result if result is not None else {}


# ============================================================================
# Internal Data Types
# ============================================================================


@dataclass(frozen=True)
class KFrameInfo:
    """
    Metadata about a K stack frame (not just ReturnFrame).

    Used to track handler boundaries, scopes, etc.

    NOTE: This is for future use. v1 only captures ReturnFrame generators.
    """

    # v1: Only "return" is used. Others reserved for future expansion.
    frame_type: str  # "return", "catch", "finally", "local", "intercept", "listen", "gather"

    # For ReturnFrame: the effect frame data
    effect_frame: EffectFrame | None = None

    # For FinallyFrame: handler info
    handler_name: str | None = None

    # For LocalFrame: env keys being restored
    env_keys: tuple[str, ...] | None = None


@dataclass
class PreCapturedFrame:
    """
    Lightweight pre-capture of generator info.

    Does NOT call linecache.getline (deferred to error path).
    Stores generator reference for identity-based dedup.

    This is captured BEFORE next(gen)/send()/throw() so we have
    generator info even if it dies during execution.
    """

    generator: Generator[Any, Any, Any]
    filename: str
    lineno: int
    function: str  # User-facing function name (from program_call if available)
    frame_kind: str


# ============================================================================
# Internal Helper Functions
# ============================================================================


def _safe_getline(filename: str, lineno: int) -> str | None:
    """Get source line without raising.

    Returns None for built-in files (filename starts with '<') or on error.
    """
    try:
        if filename.startswith("<"):
            return None
        line = linecache.getline(filename, lineno)
        return line.strip() or None
    except Exception:
        return None


def _get_function_name(
    gen: Generator[Any, Any, Any],
    program_call: KleisliProgramCall | None,
) -> str:
    """Get display function name for traceback.

    Uses program_call.function_name if available (user's @do function name),
    otherwise falls back to generator's code object name (may be internal wrapper).
    """
    if program_call is not None:
        return program_call.function_name
    return gen.gi_code.co_name


# ============================================================================
# Pre-Capture Functions (lightweight, called before generator execution)
# ============================================================================


def _is_doeff_internal_file(filename: str) -> bool:
    """Check if a filename is internal to the doeff package."""
    # Normalize path separators
    normalized = filename.replace("\\", "/")
    # Check for doeff package paths (but not examples or tests)
    if "/doeff/" in normalized:
        # Exclude test and example files
        if "/tests/" in normalized or "/examples/" in normalized:
            return False
        return True
    return False


def _find_user_generator(gen: Generator[Any, Any, Any]) -> Generator[Any, Any, Any]:
    """
    Traverse wrapper chain to find the user's generator.

    The wrapper chain is:
    - program.py's generator has 'generator_obj' (do.py's wrapper)
    - do.py's generator_wrapper has 'gen' (user's generator)

    We traverse until we find a generator NOT from the doeff package.
    """
    current = gen
    visited: set[int] = set()

    for _ in range(5):  # Limit depth to prevent infinite loops
        if id(current) in visited:
            break
        visited.add(id(current))

        # Check gi_code first (always available, even if gi_frame is None)
        code_filename = current.gi_code.co_filename
        if not _is_doeff_internal_file(code_filename):
            return current

        # If frame is available, check it too and try to traverse deeper
        if current.gi_frame is not None:
            # Try to find inner generator in locals
            locals_dict = current.gi_frame.f_locals
            inner = locals_dict.get("gen") or locals_dict.get("generator_obj")
            if inner is not None and hasattr(inner, "gi_code"):
                current = inner
                continue

        break

    return gen  # Return original if no user generator found


def _get_user_source_location(
    gen: Generator[Any, Any, Any],
    program_call: KleisliProgramCall | None,
    is_resumed: bool,
) -> tuple[str, int]:
    """
    Get the user's source location instead of internal wrapper location.

    For resumed generators: traverses the wrapper chain via frame locals.
    For unstarted generators: uses original_func from KleisliProgramCall.

    Returns:
        (filename, lineno) tuple
    """
    # For resumed generators, traverse to find user's generator via frame locals
    if is_resumed and gen.gi_frame is not None:
        user_gen = _find_user_generator(gen)
        if user_gen.gi_frame is not None:
            return user_gen.gi_frame.f_code.co_filename, user_gen.gi_frame.f_lineno

    # For unstarted generators, can't traverse frames - use original_func if available
    if program_call is not None:
        kleisli_source = getattr(program_call, "kleisli_source", None)
        if kleisli_source is not None:
            original_func = getattr(kleisli_source, "original_func", None)
            if original_func is not None and hasattr(original_func, "__code__"):
                code = original_func.__code__
                return code.co_filename, code.co_firstlineno

    # Fallback to wrapper generator's code object
    return gen.gi_code.co_filename, gen.gi_code.co_firstlineno


def pre_capture_generator(
    gen: Generator[Any, Any, Any],
    is_resumed: bool = False,
    program_call: KleisliProgramCall | None = None,
) -> PreCapturedFrame:
    """
    Pre-capture generator info BEFORE next()/send()/throw().

    This is called on the happy path but is very lightweight (~50-100ns):
    just attribute reads, no file I/O.

    Args:
        gen: The generator about to be executed
        is_resumed: False for first execution, True for resumed execution
        program_call: KleisliProgramCall providing correct function name

    Returns:
        PreCapturedFrame with generator info for use in error path

    Behavior:
        - For first execution: uses co_firstlineno, frame_kind="kleisli_entry"
        - For resumed execution: uses gi_frame.f_lineno if available, frame_kind="kleisli_yield"
        - If is_resumed=True but gi_frame is None (closed generator): frame_kind="kleisli_closed"
    """
    function = _get_function_name(gen, program_call)

    # Get user source location instead of internal wrapper location
    filename, lineno = _get_user_source_location(gen, program_call, is_resumed)

    if is_resumed and gen.gi_frame is not None:
        # Generator is paused at a yield - use current position
        return PreCapturedFrame(
            generator=gen,
            filename=filename,
            lineno=lineno,
            function=function,
            frame_kind="kleisli_yield",
        )
    if is_resumed and gen.gi_frame is None:
        # Edge case: is_resumed=True but gi_frame=None means generator was closed
        # (e.g., by a previous throw() or close()). Use definition location with
        # "kleisli_closed" to indicate unusual state.
        return PreCapturedFrame(
            generator=gen,
            filename=filename,
            lineno=lineno,
            function=function,
            frame_kind="kleisli_closed",
        )
    # Generator not started - use definition location
    return PreCapturedFrame(
        generator=gen,
        filename=filename,
        lineno=lineno,
        function=function,
        frame_kind="kleisli_entry",
    )


def precapture_to_effect_frame(
    pc: PreCapturedFrame,
    caller_gen: Generator[Any, Any, Any] | None = None,
    caller_program_call: KleisliProgramCall | None = None,
) -> EffectFrame:
    """
    Convert PreCapturedFrame to EffectFrame (called only on error path).

    This is where linecache.getline is called (file I/O only on error).

    Args:
        pc: The pre-captured frame info
        caller_gen: Generator that called this one (provides call site info)
        caller_program_call: KleisliProgramCall for caller's function name

    Returns:
        EffectFrame with full location and call site info
    """
    # Get call site from caller generator if available
    call_site_loc = None
    if caller_gen is not None and caller_gen.gi_frame is not None:
        # Use user source location for caller instead of internal wrapper
        caller_filename, caller_lineno = _get_user_source_location(
            caller_gen, caller_program_call, is_resumed=True
        )
        caller_function = _get_function_name(caller_gen, caller_program_call)
        call_site_loc = CodeLocation(
            filename=caller_filename,
            lineno=caller_lineno,
            function=caller_function,
            code=_safe_getline(caller_filename, caller_lineno),
        )

    return EffectFrame(
        location=CodeLocation(
            filename=pc.filename,
            lineno=pc.lineno,
            function=pc.function,
            code=_safe_getline(pc.filename, pc.lineno),
        ),
        frame_kind=pc.frame_kind,
        call_site=call_site_loc,
    )


# ============================================================================
# Effect Frame Capture (from K stack)
# ============================================================================


def capture_effect_frame_from_generator(
    gen: Generator[Any, Any, Any],
    program_call: KleisliProgramCall | None = None,
) -> EffectFrame | None:
    """
    Extract effect frame data from a paused generator.

    Must be called while generator is alive (gi_frame is not None).

    Args:
        gen: The generator to capture frame from
        program_call: KleisliProgramCall providing correct function name

    Returns:
        EffectFrame or None if generator frame is unavailable
    """
    try:
        frame = gen.gi_frame
        if frame is None:
            return None

        # Get user source location instead of internal wrapper location
        filename, lineno = _get_user_source_location(gen, program_call, is_resumed=True)

        # Fallback for unstarted frames where f_lineno is 0
        if lineno == 0:
            lineno = gen.gi_code.co_firstlineno

        function = _get_function_name(gen, program_call)
        code = _safe_getline(filename, lineno)

        return EffectFrame(
            location=CodeLocation(
                filename=filename,
                lineno=lineno,
                function=function,
                code=code,
            ),
            frame_kind="kleisli_yield",
            call_site=None,
            locals_snapshot=None,
        )
    except Exception:
        # Capture failure must not mask original exception
        return None


def capture_effect_frame_with_call_site(
    gen: Generator[Any, Any, Any],
    caller_gen: Generator[Any, Any, Any] | None,
    program_call: KleisliProgramCall | None = None,
    caller_program_call: KleisliProgramCall | None = None,
) -> EffectFrame | None:
    """
    Capture effect frame with call site from caller.

    Args:
        gen: The current generator (e.g., A's generator)
        caller_gen: The generator that yielded this one (e.g., main_gen)
        program_call: KleisliProgramCall for correct function name
        caller_program_call: KleisliProgramCall for caller's function name

    Returns:
        EffectFrame with call_site info, or None if frame unavailable
    """
    try:
        frame = gen.gi_frame
        if frame is None:
            return None

        # Get user source location instead of internal wrapper location
        filename, lineno = _get_user_source_location(gen, program_call, is_resumed=True)

        # Fallback for unstarted frames
        if lineno == 0:
            lineno = gen.gi_code.co_firstlineno

        function = _get_function_name(gen, program_call)

        # Where this generator was called from (if we have a caller)
        call_site_loc = None
        if caller_gen is not None and caller_gen.gi_frame is not None:
            caller_filename, caller_lineno = _get_user_source_location(
                caller_gen, caller_program_call, is_resumed=True
            )
            caller_function = _get_function_name(caller_gen, caller_program_call)
            call_site_loc = CodeLocation(
                filename=caller_filename,
                lineno=caller_lineno,
                function=caller_function,
                code=_safe_getline(caller_filename, caller_lineno),
            )

        return EffectFrame(
            location=CodeLocation(
                filename=filename,
                lineno=lineno,
                function=function,
                code=_safe_getline(filename, lineno),
            ),
            frame_kind="kleisli_yield",
            call_site=call_site_loc,
        )
    except Exception:
        return None


def capture_effect_frames_from_k_with_ids(
    K: Kontinuation,
) -> tuple[tuple[EffectFrame, ...], set[int]]:
    """
    Capture effect frames AND generator ids for deduplication.

    Includes call-site capture: each frame gets call-site info from its caller.
    Uses ReturnFrame.program_call for correct function names.

    Args:
        K: The continuation stack [innermost, ..., outermost]

    Returns:
        Tuple of:
        - effect_frames: Captured frames in outermost→innermost order
        - generator_ids: Set of id(generator) for each captured frame
    """
    from doeff.cesk import ReturnFrame

    frames: list[EffectFrame] = []
    generator_ids: set[int] = set()

    # Extract only ReturnFrames for caller mapping
    return_frames = [f for f in K if isinstance(f, ReturnFrame)]

    for i, rf in enumerate(return_frames):
        generator_ids.add(id(rf.generator))

        # The caller is the NEXT frame in the list (outerward)
        # K is [innermost, ..., outermost], so index i+1 is more outerward
        caller_gen = return_frames[i + 1].generator if i + 1 < len(return_frames) else None
        caller_pc = getattr(return_frames[i + 1], "program_call", None) if i + 1 < len(return_frames) else None

        # Get program_call for this frame if available
        program_call = getattr(rf, "program_call", None)

        ef = capture_effect_frame_with_call_site(
            rf.generator, caller_gen, program_call, caller_pc
        )
        if ef is not None:
            frames.append(ef)

    # Reverse for standard traceback order (outermost first)
    return tuple(reversed(frames)), generator_ids


# ============================================================================
# Python Frame Capture (from exception traceback)
# ============================================================================


def capture_python_frames_from_traceback(tb: Any) -> tuple[PythonFrame, ...]:
    """
    Capture Python frames from an exception's traceback.

    Returns frames in outermost→innermost order (same as `traceback.format_tb`),
    where the raise site is the LAST frame.

    Note: Python's `__traceback__` chain is already in outermost→innermost order
    (tb starts at outermost, tb_next moves toward raise site). We collect frames
    in traversal order to match standard traceback format.

    Args:
        tb: The traceback object (exception.__traceback__)

    Returns:
        Tuple of PythonFrame in outermost→innermost order
    """
    frames: list[PythonFrame] = []

    while tb is not None:
        try:
            frame = tb.tb_frame
            filename = frame.f_code.co_filename
            lineno = tb.tb_lineno
            function = frame.f_code.co_name
            code = _safe_getline(filename, lineno)

            frames.append(
                PythonFrame(
                    location=CodeLocation(
                        filename=filename,
                        lineno=lineno,
                        function=function,
                        code=code,
                    ),
                    locals_snapshot=None,
                )
            )
        except Exception:
            # Skip problematic frames
            pass

        tb = tb.tb_next

    # No reversal needed - traceback chain is already outermost→innermost
    return tuple(frames)


# ============================================================================
# Main Capture Function
# ============================================================================


def capture_traceback(
    K: Kontinuation,
    ex: BaseException,
    pre_captured: PreCapturedFrame | None = None,
) -> CapturedTraceback:
    """
    Capture complete traceback data.

    Args:
        K: The continuation stack (may not include current generator if it died)
        ex: The exception
        pre_captured: Pre-captured frame info (generator ref + location, NO file I/O yet)

    Returns:
        CapturedTraceback with effect frames, python frames, and exception info
    """
    from doeff.cesk import ReturnFrame

    effect_frames, captured_gen_ids = capture_effect_frames_from_k_with_ids(K)

    # Add pre_captured frame if not already in K (dedup by generator identity)
    if pre_captured is not None:
        if id(pre_captured.generator) not in captured_gen_ids:
            # Get call site from innermost K frame (if available)
            return_frames = [f for f in K if isinstance(f, ReturnFrame)]
            caller_gen = return_frames[0].generator if return_frames else None
            caller_pc = getattr(return_frames[0], "program_call", None) if return_frames else None

            # Convert to EffectFrame with call site (linecache called here)
            current_frame = precapture_to_effect_frame(pre_captured, caller_gen, caller_pc)
            effect_frames = effect_frames + (current_frame,)

    python_frames = capture_python_frames_from_traceback(ex.__traceback__)

    return CapturedTraceback(
        effect_frames=effect_frames,
        python_frames=python_frames,
        exception_type=type(ex).__name__,
        exception_message=str(ex),
        exception_args=ex.args,
        exception=ex,
        capture_timestamp=time.time(),
    )


def capture_traceback_safe(
    K: Kontinuation,
    ex: BaseException,
    pre_captured: PreCapturedFrame | None = None,
) -> CapturedTraceback | None:
    """
    Capture traceback with error recovery.

    Returns None if capture fails entirely.
    Raises only for MemoryError (critical resource exhaustion).
    Original exception is preserved for all other failures.

    Args:
        K: The continuation stack
        ex: The exception
        pre_captured: Pre-captured frame info

    Returns:
        CapturedTraceback or None if capture failed
    """
    try:
        return capture_traceback(K, ex, pre_captured=pre_captured)
    except MemoryError:
        # Memory errors should propagate
        raise
    except Exception as capture_error:
        # Log but don't mask original exception
        warnings.warn(
            f"Failed to capture traceback: {capture_error}",
            RuntimeWarning,
            stacklevel=2,
        )
        return None


# ============================================================================
# Format Functions (Public API)
# ============================================================================


def format_traceback(tb: CapturedTraceback | None) -> str:
    """
    Format captured traceback for human-readable display.

    Returns a string similar to Python's standard traceback format,
    but with effect chain shown separately from Python chain.

    Args:
        tb: The CapturedTraceback to format, or None

    Returns:
        Formatted string. If tb is None, returns "(no captured traceback)".

    Format:
        Effect Traceback (Kleisli call chain):
          File "filename", line N, in function
            code line
          ...

        Python Traceback (most recent call last):
          File "filename", line N, in function
            code line
          ...

        ExceptionType: exception message
    """
    if tb is None:
        return "(no captured traceback)"

    lines: list[str] = []

    # Effect Traceback section
    lines.append("Effect Traceback (Kleisli call chain):")
    if not tb.effect_frames:
        lines.append("  (no effect frames)")
    else:
        for ef in tb.effect_frames:
            loc = ef.location
            lines.append(f'  File "{loc.filename}", line {loc.lineno}, in {loc.function}')
            # Code line handling: skip for built-in files, show placeholder for missing
            if not loc.filename.startswith("<"):
                if loc.code is not None:
                    lines.append(f"    {loc.code}")
                else:
                    lines.append("    <source unavailable>")

    # Blank line between sections
    lines.append("")

    # Python Traceback section
    lines.append("Python Traceback (most recent call last):")
    if not tb.python_frames:
        lines.append("  (no python frames)")
    else:
        for pf in tb.python_frames:
            loc = pf.location
            lines.append(f'  File "{loc.filename}", line {loc.lineno}, in {loc.function}')
            # Code line handling: skip for built-in files, show placeholder for missing
            if not loc.filename.startswith("<"):
                if loc.code is not None:
                    lines.append(f"    {loc.code}")
                else:
                    lines.append("    <source unavailable>")

    # Blank line before exception
    lines.append("")

    # Exception line
    exc_line = f"{tb.exception_type}: {tb.exception_message}"
    lines.append(exc_line)

    # ExceptionGroup handling (Python 3.11+)
    if sys.version_info >= (3, 11) and isinstance(tb.exception, ExceptionGroup):
        _format_exception_group_details(tb.exception, lines)

    return "\n".join(lines)


def _format_exception_group_details(eg: BaseException, lines: list[str]) -> None:
    """Format ExceptionGroup sub-exceptions with tree-style separators."""
    if not hasattr(eg, "exceptions"):
        return

    sub_exceptions = eg.exceptions
    for i, sub_ex in enumerate(sub_exceptions):
        is_last = i == len(sub_exceptions) - 1
        prefix = "+---" if not is_last else "+---"

        if isinstance(sub_ex, BaseExceptionGroup):
            # Nested group - show as summary
            lines.append(f"  {prefix} {type(sub_ex).__name__}: {sub_ex.args[0]} ({len(sub_ex.exceptions)} sub-exceptions)")
        else:
            lines.append(f"  {prefix} {type(sub_ex).__name__}: {sub_ex}")


def format_traceback_short(tb: CapturedTraceback | None) -> str:
    """
    One-line summary of captured traceback.

    Format: func1 -> func2 -> func3: ExceptionType: message

    Args:
        tb: The CapturedTraceback to format, or None

    Returns:
        One-line summary. If tb is None, returns "(no captured traceback)".
        Newlines in exception message are replaced with "\\n".

    Rules:
        - Effect frame function names only, joined with " -> "
        - Empty effect_frames: shows "<top-level>"
        - Exception type/message: always preserved in full (never truncated)
        - Effect chain truncation (only if needed):
            - If joined string > 60 chars: shows "first -> ... -> last"
            - Single function names are never truncated
    """
    if tb is None:
        return "(no captured traceback)"

    # Build function chain
    if not tb.effect_frames:
        chain = "<top-level>"
    else:
        func_names = [ef.location.function for ef in tb.effect_frames]

        # Check if truncation needed
        joined = " -> ".join(func_names)
        if len(joined) > 60 and len(func_names) > 2:
            # Truncate: keep first and last
            chain = f"{func_names[0]} -> ... -> {func_names[-1]}"
        else:
            chain = joined

    # Exception message with newlines escaped
    message = tb.exception_message.replace("\n", "\\n")

    return f"{chain}: {tb.exception_type}: {message}"


def to_dict(tb: CapturedTraceback | None) -> dict[str, Any] | None:
    """
    Convert CapturedTraceback to JSON-serializable dict for logging/transport.

    Args:
        tb: The CapturedTraceback to convert, or None

    Returns:
        JSON-serializable dict, or None if tb is None.

    Schema (version 1.0):
        {
            "version": "1.0",
            "effect_frames": [...],
            "python_frames": [...],
            "exception": {...},
            "metadata": {...}
        }

    Notes:
        - locals_snapshot is OMITTED (internal/debug only)
        - For ExceptionGroup: adds is_group, group_count, sub_exceptions fields
    """
    if tb is None:
        return None

    def location_to_dict(loc: CodeLocation) -> dict[str, Any]:
        return {
            "filename": loc.filename,
            "lineno": loc.lineno,
            "function": loc.function,
            "code": loc.code,
        }

    def effect_frame_to_dict(ef: EffectFrame) -> dict[str, Any]:
        result: dict[str, Any] = {
            "location": location_to_dict(ef.location),
            "frame_kind": ef.frame_kind,
            "call_site": location_to_dict(ef.call_site) if ef.call_site else None,
        }
        # locals_snapshot is intentionally omitted
        return result

    def python_frame_to_dict(pf: PythonFrame) -> dict[str, Any]:
        result: dict[str, Any] = {
            "location": location_to_dict(pf.location),
        }
        # locals_snapshot is intentionally omitted
        return result

    def safe_serialize(obj: Any, depth: int = 0) -> Any:
        """Best-effort JSON serialization with circular reference protection."""
        if depth > 10:
            return "<max depth exceeded>"

        if obj is None or isinstance(obj, (bool, int, float, str)):
            return obj

        if isinstance(obj, tuple):
            return [safe_serialize(item, depth + 1) for item in obj]

        if isinstance(obj, list):
            return [safe_serialize(item, depth + 1) for item in obj]

        if isinstance(obj, dict):
            return {
                str(k): safe_serialize(v, depth + 1)
                for k, v in obj.items()
            }

        return "<unserializable>"

    # Build exception dict
    exception_dict: dict[str, Any] = {
        "type": tb.exception_type,
        "qualified_type": f"{type(tb.exception).__module__}.{type(tb.exception).__name__}",
        "message": tb.exception_message,
    }

    # Handle exception args - special case for ExceptionGroup
    if sys.version_info >= (3, 11) and isinstance(tb.exception, ExceptionGroup):
        # For ExceptionGroup, args[0] is the message, args[1] is the exception list
        # We only serialize the message
        exception_dict["args"] = safe_serialize([tb.exception.args[0]] if tb.exception.args else [])
        exception_dict["is_group"] = True
        exception_dict["group_count"] = len(tb.exception.exceptions)
        exception_dict["sub_exceptions"] = [
            {
                "type": type(sub_ex).__name__,
                "message": str(sub_ex),
            }
            for sub_ex in tb.exception.exceptions
        ]
    else:
        exception_dict["args"] = safe_serialize(tb.exception_args)
        # For non-groups, is_group/group_count/sub_exceptions are OMITTED (not set to None/false)

    return {
        "version": "1.0",
        "effect_frames": [effect_frame_to_dict(ef) for ef in tb.effect_frames],
        "python_frames": [python_frame_to_dict(pf) for pf in tb.python_frames],
        "exception": exception_dict,
        "metadata": {
            "capture_timestamp": tb.capture_timestamp,
            "interpreter_version": tb.interpreter_version,
        },
    }


# ============================================================================
# Public Exports
# ============================================================================

__all__ = [
    # Data types
    "CapturedTraceback",
    "CodeLocation",
    "EffectFrame",
    "PythonFrame",
    # Format functions
    "format_traceback",
    "format_traceback_short",
    "to_dict",
]
