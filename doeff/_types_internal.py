"""
Core types for the doeff effects system.

This module contains the foundational types with zero internal dependencies.
"""


import json
import traceback
import warnings
from abc import abstractmethod
from collections.abc import Callable, Generator, Hashable, Iterable, Iterator
from dataclasses import dataclass, field
from functools import wraps
from pprint import pformat
from typing import (
    TYPE_CHECKING,
    Any,
    Protocol,
    TypeAlias,
    TypeVar,
    cast,
    runtime_checkable,
)

# from doeff import CachePut

# Import Program for type alias, but avoid circular imports
if TYPE_CHECKING:
    from phart.styles import NodeStyle

    from doeff.program import Program

# Core graph/result primitives
from doeff._vendor import (
    NOTHING,
    Err,
    FrozenDict,
    Maybe,
    Nothing,
    Ok,
    Result,
    Some,
    TraceError,
    WGraph,
    WNode,
    WStep,
    trace_err,
)
from doeff.utils import BoundedLog

# Type variables
T = TypeVar("T")
U = TypeVar("U")

# Reader environment key type (ask/local).
EnvKey: TypeAlias = Hashable

# ============================================
# Repr Truncation Configuration
# ============================================

# Default limit for truncating repr strings in RunResult.__repr__.
# This prevents terminal freeze when displaying large objects.
DEFAULT_REPR_LIMIT = 1000

# Environment key to configure the repr limit via ask effect.
# Set to None to disable truncation, or an int for custom limit (1000-10000 recommended).
REPR_LIMIT_KEY = "__repr_limit__"


def _truncate_repr(obj: object, limit: int | None) -> str:
    """
    Return a truncated repr of obj.

    Args:
        obj: The object to represent.
        limit: Maximum length of the repr string. If None, no truncation.

    Returns:
        The repr string, truncated with a suffix if it exceeds limit.
        The suffix includes a hint about configuring the limit via env.
    """
    text = repr(obj)
    if limit is None or len(text) <= limit:
        return text
    # Truncate and add helpful message about configuration
    truncated = text[: max(0, limit)]
    return (
        f"{truncated}... "
        f"[truncated {len(text) - limit} chars; "
        f"set env['{REPR_LIMIT_KEY}'] to adjust]"
    )


# ============================================
# Trace Location
# ============================================


@dataclass(frozen=True)
class TraceLocation:
    """Location metadata for display and diagnostics."""

    filename: str
    line: int
    function: str
    code: str | None = None

    def format_location(self) -> str:
        return f"{self.filename}:{self.line} in {self.function}"


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
        max_lines: int = 18,
    ) -> list[str]:
        """Return sanitized traceback lines with optional condensation."""

        sanitized = self._sanitize_lines()
        if not condensed or max_lines is None or len(sanitized) <= max_lines or not sanitized:
            return sanitized

        return _condense_traceback_lines(sanitized, max_lines)

    def format(
        self,
        *,
        condensed: bool = False,
        max_lines: int = 18,
    ) -> str:
        """Render the traceback as a single string."""

        return "\n".join(self.lines(condensed=condensed, max_lines=max_lines))

    def get_raise_location(self) -> "RaiseLocation | None":
        """Extract the innermost user code frame where exception was raised.

        Returns RaiseLocation with (filename, line, function, code) or None if no frame found.
        """
        # Get frames from the traceback exception (innermost last)
        frames = list(self.traceback.stack)
        if not frames:
            return None

        # Search from innermost frame outward for a user code frame
        for frame in reversed(frames):
            if _is_user_code_path(frame.filename):
                line_no = frame.lineno if frame.lineno is not None else 0
                return RaiseLocation(frame.filename, line_no, frame.name, frame.line)

        # Fall back to innermost frame even if not user code
        frame = frames[-1]
        line_no = frame.lineno if frame.lineno is not None else 0
        return RaiseLocation(frame.filename, line_no, frame.name, frame.line)


@dataclass(frozen=True)
class RaiseLocation:
    """Location where an exception was raised."""

    filename: str
    line: int
    function: str
    code: str | None


@dataclass(frozen=True)
class _TracebackSplit:
    """Split traceback into header and body sections."""

    header: list[str]
    body: list[str]


@dataclass(frozen=True)
class _HeadTailLengths:
    """Head and tail line counts for traceback display."""

    head_lines: int
    tail_lines: int


def _condense_traceback_lines(lines: list[str], max_lines: int) -> list[str]:
    split = _split_traceback_header(lines)
    header, body = split.header, split.body
    if not body:
        return header[:max_lines]

    available = max_lines - len(header)
    if available <= 0:
        return header[:max_lines]

    fallback = header + body[:available]
    if len(body) <= available:
        return fallback

    available_frames = available - 1
    if available <= 4 or available_frames <= 0:
        return header + body[-available:]

    lengths = _choose_head_tail_lengths(body, available_frames)
    if lengths.tail_lines <= 0 or lengths.head_lines + lengths.tail_lines >= len(body):
        return fallback

    ellipsis_line = "    ..."
    return header + body[: lengths.head_lines] + [ellipsis_line] + body[-lengths.tail_lines :]


def _split_traceback_header(lines: list[str]) -> _TracebackSplit:
    if lines and lines[0].strip().startswith("Traceback"):
        return _TracebackSplit(header=[lines[0]], body=lines[1:])
    return _TracebackSplit(header=[], body=lines)


def _choose_head_tail_lengths(body: list[str], available_frames: int) -> _HeadTailLengths:
    tail_lines = _initial_tail_length(available_frames)
    available = available_frames
    if tail_lines >= available:
        return _HeadTailLengths(head_lines=available, tail_lines=0)

    head_lines = max(2, available - tail_lines)
    lengths = _rebalance_for_user_frames(body, head_lines, tail_lines)
    return lengths


def _initial_tail_length(available_frames: int) -> int:
    tail_min = 4
    if available_frames <= tail_min:
        return available_frames

    tail_lines = max(tail_min, available_frames // 2)
    if tail_lines > available_frames - 2:
        return max(available_frames - 2, 2)
    return tail_lines


def _rebalance_for_user_frames(
    body: list[str],
    head_lines: int,
    tail_lines: int,
    desired_user_frames: int = 3,
    tail_min: int = 4,
) -> _HeadTailLengths:
    user_span_end = _find_user_span_end(body, desired_user_frames)
    available = head_lines + tail_lines

    if user_span_end is None or user_span_end <= head_lines:
        return _HeadTailLengths(head_lines=head_lines, tail_lines=tail_lines)

    lengths = _grow_head_with_tail(
        head_lines,
        tail_lines,
        user_span_end - head_lines,
        tail_min,
    )
    head_lines, tail_lines = lengths.head_lines, lengths.tail_lines

    if user_span_end > head_lines:
        head_lines = min(user_span_end, available)
        tail_lines = max(0, available - head_lines)

    if head_lines < 2:
        head_lines = min(2, available)
        tail_lines = max(0, available - head_lines)

    return _HeadTailLengths(head_lines=head_lines, tail_lines=tail_lines)


def _grow_head_with_tail(
    head_lines: int,
    tail_lines: int,
    extra_needed: int,
    tail_min: int,
) -> _HeadTailLengths:
    if extra_needed <= 0:
        return _HeadTailLengths(head_lines=head_lines, tail_lines=tail_lines)

    reservable = max(0, tail_lines - tail_min)
    take = min(extra_needed, reservable)
    head_lines += take
    tail_lines -= take
    extra_needed -= take

    if extra_needed > 0:
        spare_tail = max(0, tail_lines - 2)
        take = min(extra_needed, spare_tail)
        head_lines += take
        tail_lines -= take

    return _HeadTailLengths(head_lines=head_lines, tail_lines=tail_lines)


def _find_user_span_end(body: list[str], desired: int) -> int | None:
    frames = _traceback_frame_spans(body)
    user_frames_seen = 0

    for start, end in frames:
        path = _extract_traceback_path(body[start])
        if path and not _is_library_traceback_path(path):
            user_frames_seen += 1
            if user_frames_seen >= desired:
                return end

    return None


def _traceback_frame_spans(body: list[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(body):
        line = body[index]
        if line.lstrip().startswith("File "):
            start = index
            index += 1
            while index < len(body) and not body[index].lstrip().startswith("File "):
                index += 1
            spans.append((start, index))
        else:
            index += 1
    return spans


def _extract_traceback_path(line: str) -> str | None:
    start = line.find('"')
    if start == -1:
        return None
    end = line.find('"', start + 1)
    if end == -1:
        return None
    return line[start + 1 : end]


def _is_library_traceback_path(path: str) -> bool:
    lowered = path.lower()
    if "/site-packages/" in lowered:
        return True
    return "/python" in lowered and "/repos/" not in lowered


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
class EffectFailureError(Exception):
    """Complete error information for a failed effect.

    Captures both the failing effect and runtime traceback context.
    """

    effect: "Effect"
    cause: BaseException  # The original exception that caused the failure
    runtime_traceback: CapturedTraceback | None = None
    traceback_data: Any | None = None
    call_stack_snapshot: tuple["CallFrame", ...] = field(
        default_factory=tuple
    )  # Program call stack at failure time

    def __str__(self) -> str:
        """Format the error for display."""
        return (
            f"Effect '{self.effect.__class__.__name__}' failed\n"
            f"Caused by: {self.cause.__class__.__name__}: {self.cause}"
        )

    def __post_init__(self) -> None:
        """Capture runtime traceback if not provided."""
        if self.runtime_traceback is None and isinstance(self.cause, BaseException):
            captured = get_captured_traceback(self.cause)
            if captured is None:
                captured = capture_traceback(self.cause)
            self.runtime_traceback = captured


EffectFailure = EffectFailureError


# ============================================
# Core Effect Type
# ============================================

from doeff.program import DoExpr, Program, ProgramBase

try:
    from doeff_vm import EffectBase as _RustEffectBase
except Exception:  # pragma: no cover - fallback for docs/type tooling without native module
    _RustEffectBase = object


def _is_rust_effect_subclass(subclass: type[Any]) -> bool:
    if _RustEffectBase is object:
        return False

    try:
        return issubclass(subclass, _RustEffectBase)
    except TypeError:
        return False


class _EffectBaseMeta(type):
    def __subclasscheck__(cls, subclass: type[Any]) -> bool:
        effect_base = globals().get("EffectBase")
        if effect_base is not None and cls is effect_base and _is_rust_effect_subclass(subclass):
            return True
        return super().__subclasscheck__(subclass)

    def __instancecheck__(cls, instance: Any) -> bool:
        return cls.__subclasscheck__(instance.__class__) or super().__instancecheck__(instance)


@runtime_checkable
class Effect(Protocol):
    """Protocol implemented by all effect values."""


@dataclass(frozen=True, kw_only=True)
class EffectBase(_RustEffectBase, metaclass=_EffectBaseMeta):
    """Base dataclass implementing :class:`Effect` semantics.

    SPEC-TYPES-001 Rev 11: EffectBase is effect data (EffectValue), not DoExpr.
    Effects are requests (pure data), not control computations.
    Dispatch occurs when lifted into control IR via Perform(effect).
    """

    __doeff_effect_base__: bool = field(default=True, init=False, repr=False, compare=False)

    def map(self, f: Callable[[Any], Any]) -> Program[Any]:
        raise TypeError(
            "Effect values do not support direct map(); lift with Perform(effect) "
            "or Program.lift(effect) before composition"
        )

    def flat_map(self, f: Callable[[Any], Any]) -> Program[Any]:
        raise TypeError(
            "Effect values do not support direct flat_map(); lift with Perform(effect) "
            "or Program.lift(effect) before composition"
        )

    def and_then_k(self, binder: Callable[[Any], Any]) -> Program[Any]:
        raise TypeError(
            "Effect values do not support direct and_then_k(); lift with Perform(effect) "
            "or Program.lift(effect) before composition"
        )


@dataclass(frozen=True, kw_only=True)
class NullEffect(EffectBase):
    """Placeholder effect for exceptions raised directly (not via yield Fail)."""


# Type alias for generators used in @do functions
# This simplifies the verbose Generator[Union[Effect, Program], Any, T] pattern
if TYPE_CHECKING:
    EffectGenerator = Generator[Effect | "Program", Any, T]
else:
    # Runtime version to avoid importing Program
    EffectGenerator = Generator[Effect | Any, Any, T]


# ============================================
# Program Type
# ============================================

# The core monad - a generator that yields Effects and returns a value
ProgramGenerator = Generator[Effect, Any, T]


# ============================================
# Call Frame - tracks program call stack
# ============================================


@dataclass(frozen=True)
class CallFrame:
    """
    Represents a single frame in the program call stack.

    Tracks which KleisliProgram was called with what arguments,
    enabling call tree reconstruction for effect tracking.
    """

    kleisli: Any = None  # KleisliProgram (type hint avoided to prevent circular import)
    function_name: str = "<unknown>"
    args: tuple[Any, ...] = field(default_factory=tuple)
    kwargs: dict[str, Any] = field(default_factory=dict)
    depth: int = 0  # Depth in the call stack (0 = top-level)
    source_file: str | None = None
    source_line: int | None = None
    args_repr: str | None = None


@dataclass(frozen=True)
class EffectObservation:
    """Lightweight record of an observed effect during execution."""

    effect_type: str
    key: EnvKey | None
    context: TraceLocation | None = None
    call_stack_snapshot: tuple[CallFrame, ...] = field(default_factory=tuple)


# ============================================
# Failure Introspection Types
# ============================================


@dataclass(frozen=True)
class EffectFailureInfo:
    """Summary of a single EffectFailure instance within an error chain."""

    effect: "Effect"
    cause: BaseException | None
    runtime_trace: CapturedTraceback | None
    cause_trace: CapturedTraceback | None
    traceback_data: Any | None = None
    call_stack_snapshot: tuple["CallFrame", ...] = field(
        default_factory=tuple
    )  # Program call stack at failure


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
                traceback_data = getattr(exc, "traceback_data", None)
                if cause_exc is not None:
                    seen_exceptions.add(id(cause_exc))
                # Get call stack snapshot from EffectFailure if available
                call_stack = getattr(exc, "call_stack_snapshot", ())
                entries.append(
                    EffectFailureInfo(
                        effect=exc.effect,
                        cause=cause_exc,
                        runtime_trace=runtime_trace,
                        cause_trace=capture(cause_exc),
                        traceback_data=traceback_data,
                        call_stack_snapshot=call_stack,
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


@runtime_checkable
class RunResult(Protocol[T]):
    """Protocol defining the RunResult interface.

    Runtime implementations (notably doeff_vm.RunResult) must conform to this protocol.
    """

    @property
    def value(self) -> T: ...

    @property
    def result(self) -> Result[T]: ...

    @property
    def raw_store(self) -> dict[str, Any]: ...

    @property
    def error(self) -> BaseException: ...

    @property
    def traceback_data(self) -> Any | None: ...

    def is_ok(self) -> bool: ...

    def is_err(self) -> bool: ...


@dataclass(frozen=True)
class _DepAskUsageRecord:
    effect_type: str
    key: EnvKey | None
    count: int
    first_context: TraceLocation | None


@dataclass(frozen=True)
class _DepAskStats:
    records: tuple[_DepAskUsageRecord, ...]
    keys_by_type: dict[str, tuple[EnvKey | None, ...]]

    @classmethod
    def from_observations(
        cls,
        observations: list[EffectObservation],
    ) -> "_DepAskStats":
        interesting = {"Dep", "Ask"}
        records: list[_DepAskUsageRecord] = []
        record_index: dict[tuple[str, EnvKey | None], int] = {}
        keys_by_type: dict[str, list[EnvKey | None]] = {
            effect_type: [] for effect_type in interesting
        }

        def _maybe_add_key(effect_type: str, key: EnvKey | None) -> None:
            seen_keys = keys_by_type.setdefault(effect_type, [])
            if key not in seen_keys:
                seen_keys.append(key)

        for observation in observations:
            effect_type = observation.effect_type
            if effect_type not in interesting:
                continue

            key = observation.key
            pair = (effect_type, key)
            idx = record_index.get(pair)

            if idx is None:
                record_index[pair] = len(records)
                records.append(
                    _DepAskUsageRecord(
                        effect_type=effect_type,
                        key=key,
                        count=0,
                        first_context=observation.context,
                    )
                )
                _maybe_add_key(effect_type, key)
                idx = record_index[pair]

            record = records[idx]
            context = record.first_context or observation.context
            records[idx] = _DepAskUsageRecord(
                effect_type=record.effect_type,
                key=record.key,
                count=record.count + 1,
                first_context=context,
            )

        return cls(
            records=tuple(records),
            keys_by_type={effect_type: tuple(keys) for effect_type, keys in keys_by_type.items()},
        )

    def is_empty(self) -> bool:
        return not self.records

    def keys_for(self, effect_type: str) -> tuple[EnvKey | None, ...]:
        return self.keys_by_type.get(effect_type, ())


@dataclass(frozen=True)
class RunResultDisplayContext:
    """Shared context for building RunResult display output."""

    run_result: Any
    verbose: bool
    indent_unit: str
    failure_details: RunFailureDetails | None
    dep_ask_stats: _DepAskStats

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
        return self.context.run_result._format_value(
            value,
            max_length=max_length,
        )

    def _relative_path(self, path: str) -> str:
        """Convert absolute path to relative path for cleaner display."""
        import os

        cwd = os.getcwd()
        if path.startswith(cwd):
            return path[len(cwd) + 1 :]
        # Try to extract a meaningful relative path
        parts = path.replace("\\", "/").split("/")
        # Find a recognizable directory like 'tests' or 'src'
        for i, part in enumerate(parts):
            if part in ("tests", "src", "placement", "doeff"):
                return "/".join(parts[i:])
        # Fall back to just the filename
        return parts[-1] if parts else path


class _HeaderSection(_BaseSection):
    def render(self) -> list[str]:
        return ["=" * 60, "RunResult Internal Data", "=" * 60]


class _StatusSection(_BaseSection):
    def render(self) -> list[str]:
        rr = self.context.run_result
        lines = ["📊 Result Status:"]

        if rr.is_ok():
            lines.append(self.indent(1, "✅ Success"))
            value_repr = pformat(rr.result.value, width=80, compact=False)
            # Apply truncation based on env config
            limit = rr.context.env.get(REPR_LIMIT_KEY, DEFAULT_REPR_LIMIT)
            if limit is not None and len(value_repr) > limit:
                value_repr = (
                    f"{value_repr[:limit]}...\n"
                    f"[truncated {len(value_repr) - limit} chars; "
                    f"set env['{REPR_LIMIT_KEY}'] to adjust]"
                )
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
                # Show more meaningful message for NullEffect (direct exception raise)
                if effect_name == "NullEffect":
                    lines.append(self.indent(1, "Exception raised"))
                    # Show the location where exception was raised from runtime trace
                    if head.runtime_trace:
                        loc = head.runtime_trace.get_raise_location()
                        if loc:
                            rel_path = self._relative_path(loc.filename)
                            lines.append(
                                self.indent(
                                    2,
                                    f"📍 Raised at: {rel_path}:{loc.line} in {loc.function}",
                                )
                            )
                            if loc.code:
                                lines.append(self.indent(3, loc.code.strip()))
                else:
                    lines.append(self.indent(1, f"Effect '{effect_name}' failed"))
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
                lines.append(self.indent(1, f"{exc.__class__.__name__}: {exc}"))
        else:
            lines.append(self.indent(1, f"Error: {rr.result.error!r}"))

        return lines


def _is_user_code_path(path: str) -> bool:
    """Check if a file path belongs to user code (not doeff internals)."""
    normalized = path.replace("\\", "/").lower()

    # Filter out site-packages (includes installed doeff)
    if "/site-packages/" in normalized:
        return False

    # Filter out stdlib paths
    if "/lib/python" in normalized:
        return False
    if "/frameworks/python.framework" in normalized:
        return False
    if "/.local/share/uv/python" in normalized:
        return False

    # Filter out doeff package internals (but not tests/ or examples/)
    # Check for doeff module files specifically
    if "/doeff/" in normalized:
        # Allow tests and examples
        if "/tests/" in normalized or "/examples/" in normalized:
            return True
        # Filter out doeff package source files
        doeff_internals = (
            "/doeff/_",
            "/doeff/do.py",
            "/doeff/handlers",
            "/doeff/interpreter",
            "/doeff/kleisli",
            "/doeff/program",
            "/doeff/types",
            "/doeff/utils",
            "/doeff/effects/",
        )
        for internal in doeff_internals:
            if internal in normalized:
                return False

    return True


@dataclass(frozen=True)
class _UserEffectFrame:
    """A single frame in the user effect stack."""

    filename: str
    line: int
    function: str
    code: str | None


class _UserEffectStackSection(_BaseSection):
    """Display user-friendly effect stack trace with root cause first."""

    def render(self) -> list[str]:
        # Only show in non-verbose mode - verbose mode uses _ErrorSection
        if self.context.verbose:
            return []

        details = self.context.failure_details
        if details is None or not details.entries:
            return []

        lines: list[str] = []

        # Find root cause (innermost exception that's not EffectFailure)
        root_cause = self._find_root_cause(details)
        if root_cause:
            lines.append("Root Cause:")
            lines.append(self.indent(1, f"{root_cause.__class__.__name__}: {root_cause}"))
            lines.append("")

        # Extract user code frames from the effect chain
        user_frames = self._extract_user_frames(details)
        if user_frames:
            lines.append("Effect Stack (user code):")
            lines.append("")
            for frame in user_frames:
                # Format: in path:line func_name
                rel_path = self._relative_path(frame.filename)
                lines.append(f"in {rel_path}:{frame.line} {frame.function}")
                if frame.code:
                    lines.append(self.indent(1, frame.code))

        return lines

    def _find_root_cause(self, details: RunFailureDetails) -> BaseException | None:
        """Find the innermost exception that's not an EffectFailure."""
        root_cause: BaseException | None = None

        for entry in details.entries:
            if isinstance(entry, EffectFailureInfo):
                if entry.cause and not isinstance(entry.cause, EffectFailure):
                    root_cause = entry.cause
            elif isinstance(entry, ExceptionFailureInfo):
                root_cause = entry.exception

        return root_cause

    def _extract_user_frames(self, details: RunFailureDetails) -> list[_UserEffectFrame]:
        """Extract user code frames from call stacks and runtime tracebacks."""
        frames: list[_UserEffectFrame] = []
        seen_locations: set[tuple[str, int, str]] = set()

        for entry in details.entries:
            if not isinstance(entry, EffectFailureInfo):
                continue

            # First, add frames from the program call stack (outer -> inner call chain).
            if entry.call_stack_snapshot:
                for call_frame in entry.call_stack_snapshot:
                    filename = getattr(call_frame, "source_file", None)
                    if filename is None and isinstance(call_frame, dict):
                        filename = call_frame.get("source_file")
                    line_no = getattr(call_frame, "source_line", None)
                    if line_no is None and isinstance(call_frame, dict):
                        line_no = call_frame.get("source_line")
                    function_name = getattr(call_frame, "function_name", None)
                    if function_name is None and isinstance(call_frame, dict):
                        function_name = call_frame.get("function_name")

                    if not isinstance(filename, str) or not isinstance(line_no, int):
                        continue
                    if not isinstance(function_name, str):
                        function_name = "<unknown>"
                    if not _is_user_code_path(filename):
                        continue
                    loc_key = (filename, line_no, function_name)
                    if loc_key in seen_locations:
                        continue
                    seen_locations.add(loc_key)
                    frames.append(
                        _UserEffectFrame(
                            filename=filename,
                            line=line_no,
                            function=function_name,
                            code=None,
                        )
                    )

            # For NullEffect (direct exception raise), extract frames from runtime_trace
            # This shows the actual location where the exception was raised
            effect_name = entry.effect.__class__.__name__
            if effect_name == "NullEffect" and entry.runtime_trace:
                loc = entry.runtime_trace.get_raise_location()
                if loc and _is_user_code_path(loc.filename):
                    loc_key = (loc.filename, loc.line, loc.function)
                    if loc_key not in seen_locations:
                        seen_locations.add(loc_key)
                        frames.append(
                            _UserEffectFrame(
                                filename=loc.filename,
                                line=loc.line,
                                function=loc.function,
                                code=loc.code,
                            )
                        )

        return frames


class _ErrorSection(_BaseSection):
    def render(self) -> list[str]:
        # Only show verbose error chain in verbose mode
        # Non-verbose mode uses _UserEffectStackSection for cleaner output
        if not self.context.verbose:
            return []

        details = self.context.failure_details
        if details is None or not details.entries:
            return []

        lines: list[str] = ["Error Chain (most recent first):"]

        primary_effect_idx: int | None = None
        for primary_index, primary_entry in enumerate(details.entries, start=1):
            if isinstance(primary_entry, EffectFailureInfo):
                primary_effect_idx = primary_index
                break

        for idx, entry in enumerate(details.entries, start=1):
            if idx > 1:
                lines.append("")

            if isinstance(entry, EffectFailureInfo):
                is_primary = primary_effect_idx is not None and idx == primary_effect_idx
                lines.extend(self._render_effect_entry(idx, entry, is_primary=is_primary))
            elif isinstance(entry, ExceptionFailureInfo):
                lines.extend(self._render_exception_entry(idx, entry))

        return lines

    def _render_effect_entry(
        self,
        idx: int,
        entry: EffectFailureInfo,
        *,
        is_primary: bool,
    ) -> list[str]:
        effect_name = entry.effect.__class__.__name__
        # Show more meaningful message for NullEffect (direct exception raise)
        if effect_name == "NullEffect":
            lines = [self.indent(1, f"[{idx}] Exception raised")]
        else:
            lines = [self.indent(1, f"[{idx}] Effect '{effect_name}' failed")]
        lines.extend(self._render_effect_creation_details(entry, effect_name, is_primary))
        lines.extend(self._render_effect_cause(entry))
        lines.extend(self._render_runtime_trace(entry))
        return lines

    def _render_effect_creation_details(
        self,
        entry: EffectFailureInfo,
        effect_name: str,
        is_primary: bool,
    ) -> list[str]:
        # For NullEffect, show the exception raise location from runtime_trace
        if effect_name == "NullEffect":
            if entry.runtime_trace:
                loc = entry.runtime_trace.get_raise_location()
                if loc:
                    rel_path = self._relative_path(loc.filename)
                    lines = [
                        self.indent(2, f"📍 Raised at: {rel_path}:{loc.line} in {loc.function}")
                    ]
                    if loc.code:
                        lines.append(self.indent(3, loc.code.strip()))
                    return lines
            return [self.indent(2, "📍 Raised at: <unknown>")]

        for call_frame in reversed(entry.call_stack_snapshot):
            source_file = getattr(call_frame, "source_file", None)
            if source_file is None and isinstance(call_frame, dict):
                source_file = call_frame.get("source_file")
            source_line = getattr(call_frame, "source_line", None)
            if source_line is None and isinstance(call_frame, dict):
                source_line = call_frame.get("source_line")
            function_name = getattr(call_frame, "function_name", None)
            if function_name is None and isinstance(call_frame, dict):
                function_name = call_frame.get("function_name")

            if not isinstance(source_file, str) or not isinstance(source_line, int):
                continue
            if not isinstance(function_name, str):
                function_name = "<unknown>"

            rel_path = self._relative_path(source_file)
            return [self.indent(2, f"📍 Yielded at: {rel_path}:{source_line} in {function_name}")]

        if is_primary:
            return [self.indent(2, "📍 Yielded at: <unknown>")]
        return []

    def _render_effect_cause(self, entry: EffectFailureInfo) -> list[str]:
        cause = entry.cause
        if cause is None:
            return []
        if isinstance(cause, EffectFailure):
            return [
                self.indent(
                    2,
                    "Caused by: EffectFailure (see nested entries)",
                )
            ]
        lines = [
            self.indent(
                2,
                f"Caused by: {cause.__class__.__name__}: {cause}",
            )
        ]
        if entry.cause_trace:
            lines.extend(
                self._render_trace(
                    entry.cause_trace,
                    "🔥 Cause Stack Trace",
                )
            )
        return lines

    def _render_runtime_trace(self, entry: EffectFailureInfo) -> list[str]:
        if entry.runtime_trace is None:
            return []
        effect_name = entry.effect.__class__.__name__
        if effect_name == "ResultFailEffect" and not self.context.verbose:
            return []
        return self._render_trace(
            entry.runtime_trace,
            "🔥 Execution Stack Trace",
        )

    def _render_exception_entry(self, idx: int, entry: ExceptionFailureInfo) -> list[str]:
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
        # Always include the full sanitized traceback so user frames remain visible.
        raw_frames = trace.lines(condensed=False)
        frames: list[str] = []
        for frame in raw_frames:
            if frames and frames[-1] == frame:
                continue
            frames.append(frame)
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
        lines = ["🔗 Dep/Ask Usage:"]
        stats = self.context.dep_ask_stats
        if stats.is_empty():
            lines.append(self.indent(1, "(no Dep/Ask effects observed)"))
            return lines

        limit = 40
        records = stats.records

        for idx, record in enumerate(records[:limit], start=1):
            key_label = record.key if record.key is not None else None
            key_text = f" key={key_label!r}" if key_label is not None else " key=None"
            lines.append(
                self.indent(
                    1,
                    (f"[{idx}] {record.effect_type}{key_text} (count={record.count})"),
                )
            )
            context = record.first_context
            if context is not None:
                lines.append(self.indent(2, context.format_location()))
                if context.code:
                    lines.append(self.indent(3, context.code))
            else:
                lines.append(self.indent(2, "<location unavailable>"))

        if len(records) > limit:
            remaining = len(records) - limit
            lines.append(self.indent(1, f"... and {remaining} more entries"))

        return lines


class _EffectKeysSection(_BaseSection):
    def render(self) -> list[str]:
        stats = self.context.dep_ask_stats
        lines = ["🔑 Dep/Ask Keys:"]

        if stats.is_empty():
            lines.append(self.indent(1, "(no Dep/Ask keys recorded)"))
            return lines

        for effect_type in ("Dep", "Ask"):
            keys = stats.keys_for(effect_type)
            if keys:
                formatted = ", ".join(repr(key) if key is not None else "None" for key in keys)
            else:
                formatted = "(no keys)"
            lines.append(self.indent(1, f"{effect_type} keys: {formatted}"))

        return lines


class _CallTreeSection(_BaseSection):
    def render(self) -> list[str]:
        observations = self.context.run_result.effect_observations
        if not observations:
            return []

        from doeff.analysis import EffectCallTree  # Local import to avoid cycle

        tree = EffectCallTree.from_observations(observations)
        ascii_tree = tree.visualize_ascii()

        if ascii_tree == "(no effects)":
            return []

        lines = ["🌳 Effect Call Tree:"]
        lines.extend(self.indent(1, line) for line in ascii_tree.splitlines())
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
        env = self.context.run_result.env
        if not env:
            return []

        # Get all keys requested via Dep or Ask
        dep_keys = set(self.context.dep_ask_stats.keys_for("Dep"))
        ask_keys = set(self.context.dep_ask_stats.keys_for("Ask"))
        requested_keys = dep_keys | ask_keys

        # Separate environment into used and redundant
        used_items = [(k, v) for k, v in env.items() if k in requested_keys]
        redundant_items = [(k, v) for k, v in env.items() if k not in requested_keys]

        lines = ["🌍 Environment:"]

        # Show used environment variables
        if used_items:
            lines.append(self.indent(1, "Used:"))
            for key, value in used_items[:10]:
                value_str = self.format_value(value, max_length=100)
                lines.append(self.indent(2, f"{key}: {value_str}"))
            if len(used_items) > 10:
                remaining = len(used_items) - 10
                lines.append(self.indent(2, f"... and {remaining} more items"))

        # Show redundant environment variables (not requested)
        if redundant_items:
            lines.append(self.indent(1, "Redundant (not requested):"))
            for key, value in redundant_items[:10]:
                value_str = self.format_value(value, max_length=100)
                lines.append(self.indent(2, f"{key}: {value_str}"))
            if len(redundant_items) > 10:
                remaining = len(redundant_items) - 10
                lines.append(self.indent(2, f"... and {remaining} more items"))

        return lines


class _SummarySection(_BaseSection):
    def render(self) -> list[str]:
        rr = self.context.run_result
        lines = ["=" * 60, "Summary:"]
        status = "✅ OK" if rr.is_ok() else "❌ Error"
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
            _UserEffectStackSection(self.context),  # User-friendly stack (non-verbose)
            _ErrorSection(self.context),  # Verbose error chain (verbose only)
            _StateSection(self.context),
            _SharedStateSection(self.context),
            _LogSection(self.context),
            _EffectUsageSection(self.context),
            _EffectKeysSection(self.context),
            _CallTreeSection(self.context),
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
    log: BoundedLog

    def __iter__(self) -> Iterator[Any]:
        """Make ListenResult unpackable as a tuple (value, log)."""
        return iter([self.value, self.log])


def _intercept_value(value: Any, transform: Callable[[Effect], Effect | Program]) -> Any:
    """Recursively intercept Programs embedded within ``value``."""

    from doeff.program import ProgramBase  # Local import to avoid circular dependency

    result = value
    if isinstance(value, ProgramBase):
        result = value.intercept(transform)
    elif isinstance(value, dict):
        result = _intercept_mapping(value, transform)
    elif isinstance(value, tuple):
        # Intercept tuple items directly (inlined to avoid tuple return)
        new_items = tuple(_intercept_value(item, transform) for item in value)
        result = new_items if new_items != value else value
    elif isinstance(value, list):
        result = [_intercept_value(item, transform) for item in value]
    elif isinstance(value, set):
        result = {_intercept_value(item, transform) for item in value}
    elif isinstance(value, frozenset):
        result = frozenset(_intercept_value(item, transform) for item in value)
    elif callable(value):
        result = _wrap_callable(value, transform)

    return result


def _intercept_mapping(
    mapping: dict[Any, Any], transform: Callable[[Effect], Effect | Program]
) -> dict[Any, Any]:
    changed = False
    new_items: dict[Any, Any] = {}
    for key, item in mapping.items():
        new_item = _intercept_value(item, transform)
        if new_item is not item:
            changed = True
        new_items[key] = new_item
    return new_items if changed else mapping


def _wrap_callable(
    func: Callable[..., Any], transform: Callable[[Effect], Effect | Program]
) -> Callable[..., Any]:
    """Wrap callable so that any Program it returns is intercepted."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = func(*args, **kwargs)
        return _intercept_value(result, transform)

    return wrapper


__all__ = [
    "NOTHING",
    "Effect",
    "EffectFailure",
    "EffectFailureError",
    "EffectGenerator",
    "EffectObservation",
    "EnvKey",
    "Err",
    "FrozenDict",
    "ListenResult",
    "Maybe",
    "Nothing",
    "Ok",
    "Program",
    "ProgramBase",
    "Result",
    "RunResult",
    "Some",
    "TraceError",
    "WGraph",
    "WNode",
    "WStep",
    "trace_err",
]
