"""
Vendored minimal types from sge_hub.monads.state_graph_future_result
These types are ported to avoid circular dependencies.
Original source: sge-hub/src/sge_hub/monads/state_graph_future_result/
"""

from __future__ import annotations

import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Final, Generic, NoReturn, TypeVar, cast

from frozendict import frozendict

# =========================================================
# Type Vars
# =========================================================
T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)
U = TypeVar("U")


class Result(Generic[T_co]):
    """Sum type representing either a successful value or an error."""

    __slots__ = ()

    def is_ok(self) -> bool:
        """Return ``True`` when the result is successful."""

        return isinstance(self, Ok)

    def is_err(self) -> bool:
        """Return ``True`` when the result represents a failure."""

        return isinstance(self, Err)

    def ok(self) -> T_co | None:
        """Return the contained value, or ``None`` if this is an error."""

        if isinstance(self, Ok):
            return self.value
        return None

    def err(self) -> Exception | None:
        """Return the contained error, or ``None`` if this is a success."""

        if isinstance(self, Err):
            return self.error
        return None

    def expect(self, message: str) -> T_co:
        """Return the value or raise the error with a custom message."""

        if isinstance(self, Ok):
            return self.value

        if message:
            raise RuntimeError(f"{message}: {self.error}") from self.error
        raise self.error

    def unwrap(self) -> T_co:
        """Return the value or raise the stored error."""

        if isinstance(self, Ok):
            return self.value
        raise self.error

    def unwrap_err(self) -> Exception:
        """Return the error or raise ``RuntimeError`` if this is a success."""

        if isinstance(self, Err):
            return self.error
        raise RuntimeError("Called unwrap_err on Ok value")

    def map(self, f: Callable[[T_co], U]) -> "Result[U]":
        """Apply ``f`` to the contained value if this is a success."""

        if isinstance(self, Ok):
            return Ok(f(self.value))
        return cast(Result[U], self)

    def map_err(self, f: Callable[[Exception], Exception]) -> "Result[T_co]":
        """Apply ``f`` to the contained error if this is a failure."""

        if isinstance(self, Err):
            error = f(self.error)
            if not isinstance(error, Exception):
                raise TypeError("map_err must return an Exception instance")
            return Err(error)
        return self

    def __bool__(self) -> bool:
        """Truthiness matches :meth:`is_ok`."""

        return self.is_ok()


# =========================================================
# Result / Error
# =========================================================
@dataclass(frozen=True)
class TraceError(Exception):
    """Exception with formatted traceback and creation stack."""

    exc: BaseException
    tb: str
    created_at: str | None = None

    def __str__(self) -> str:
        lines: list[str] = []
        lines.append(f"[{self.exc.__class__.__name__}] {self.exc}")
        if self.tb:
            lines.append("----- Exception Traceback -----")
            lines.append(self.tb.rstrip())
        if self.created_at:
            lines.append("----- Awaitable Created At -----")
            lines.append(self.created_at.rstrip())
        return "\n".join(lines)


def trace_err(e: BaseException, created_at: str | None = None) -> TraceError:
    """Create TraceError from exception."""
    tb_str = "".join(traceback.format_exception(e.__class__, e, e.__traceback__))
    return TraceError(e, tb_str, created_at)


@dataclass(frozen=True)
class Ok(Result[T], Generic[T]):
    """Success result."""
    value: T


@dataclass(frozen=True)
class Err(Result[NoReturn]):
    """Error result."""
    error: Exception


class Maybe(Generic[T_co]):
    """Optional value that may contain ``Some`` data or ``Nothing``."""

    __slots__ = ()

    def is_some(self) -> bool:
        """Return ``True`` when the value is present."""

        return isinstance(self, Some)

    def is_none(self) -> bool:
        """Return ``True`` when no value is present."""

        return isinstance(self, Nothing)

    def expect(self, message: str) -> T_co:
        """Return the contained value or raise ``RuntimeError`` with ``message``."""

        if isinstance(self, Some):
            return self.value
        raise RuntimeError(message or "Expected Some value, found Nothing")

    def unwrap(self) -> T_co:
        """Return the contained value or raise ``RuntimeError``."""

        if isinstance(self, Some):
            return self.value
        raise RuntimeError("Called unwrap on Nothing value")

    def unwrap_or(self, default: U) -> T_co | U:
        """Return the value if present, otherwise ``default``."""

        if isinstance(self, Some):
            return self.value
        return default

    def unwrap_or_else(self, default_fn: Callable[[], U]) -> T_co | U:
        """Return the value if present, otherwise compute a default."""

        if isinstance(self, Some):
            return self.value
        return default_fn()

    def map(self, func: Callable[[T_co], U]) -> "Maybe[U]":
        """Apply ``func`` to the contained value when present."""

        if isinstance(self, Some):
            return Some(func(self.value))
        return NOTHING

    def flat_map(self, func: Callable[[T_co], "Maybe[U]"]) -> "Maybe[U]":
        """Chain computations that themselves return ``Maybe``."""

        if isinstance(self, Some):
            result = func(self.value)
            if not isinstance(result, Maybe):
                raise TypeError("flat_map must return a Maybe instance")
            return result
        return NOTHING

    def filter(self, predicate: Callable[[T_co], bool]) -> "Maybe[T_co]":
        """Return ``self`` if the predicate passes, otherwise ``Nothing``."""

        if isinstance(self, Some) and predicate(self.value):
            return self
        return NOTHING

    def ok_or(self, error: Exception) -> Result[T_co]:
        """Convert to ``Result``, using ``error`` when empty."""

        if isinstance(self, Some):
            return Ok(self.value)
        if not isinstance(error, Exception):
            raise TypeError("ok_or expects an Exception instance")
        return Err(error)

    def ok_or_else(self, error_fn: Callable[[], Exception]) -> Result[T_co]:
        """Convert to ``Result`` using a lazily created error."""

        if isinstance(self, Some):
            return Ok(self.value)
        error = error_fn()
        if not isinstance(error, Exception):
            raise TypeError("ok_or_else must return an Exception instance")
        return Err(error)

    def to_optional(self) -> T_co | None:
        """Convert to a Python optional value."""

        if isinstance(self, Some):
            return self.value
        return None

    @classmethod
    def from_optional(cls, value: T_co | None) -> "Maybe[T_co]":
        """Create a ``Maybe`` from an optional Python value."""

        if value is None:
            return NOTHING
        return Some(value)

    def __bool__(self) -> bool:
        """Truthiness matches :meth:`is_some`."""

        return self.is_some()


@dataclass(frozen=True)
class Some(Maybe[T], Generic[T]):
    """Presence of a value."""

    value: T


class Nothing(Maybe[NoReturn]):
    """Singleton representing the absence of a value."""

    __slots__ = ()
    _instance: "Nothing" | None = None

    def __new__(cls) -> "Nothing":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "Nothing()"


NOTHING: Final[Maybe[NoReturn]] = Nothing()

# =========================================================
# Graph (Minimal Implementation)
# =========================================================
@dataclass(frozen=True)
class WNode:
    """A node in the computation graph."""
    value: Any

    def __hash__(self) -> int:
        # Simple hash for now, can be improved if needed
        return hash(id(self.value))


@dataclass(frozen=True)
class WStep:
    """A computation step in the graph."""
    inputs: tuple[WNode, ...]
    output: WNode
    meta: dict[str, Any] = field(default_factory=dict)
    _unique_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __hash__(self) -> int:
        return hash(self._unique_id)


@dataclass(frozen=True)
class WGraph:
    """Computation graph tracking dependencies."""
    last: WStep = field(default_factory=lambda: WStep((), WNode(None)))
    steps: frozenset[WStep] = field(default_factory=frozenset)

    @classmethod
    def single(cls, value: Any) -> WGraph:
        """Create a graph with a single node."""
        node = WNode(value)
        step = WStep((), node)
        return cls(last=step, steps=frozenset({step}))

    def with_last_meta(self, meta: dict[str, Any]) -> WGraph:
        """Create a new graph with updated metadata on the last step."""
        # Merge new metadata with existing metadata instead of replacing
        merged_meta = {**self.last.meta, **meta} if self.last.meta else meta
        # Create a new last step with merged metadata
        new_last = WStep(
            inputs=self.last.inputs,
            output=self.last.output,
            meta=merged_meta
        )
        # Update the steps set - remove old last, add new last
        new_steps = (self.steps - {self.last}) | {new_last}
        return WGraph(last=new_last, steps=new_steps)

    def __hash__(self) -> int:
        return hash((self.last, self.steps))


# =========================================================
# Frozen Dict (if needed)
# =========================================================
FrozenDict = frozendict

__all__ = [
    "Err",
    "FrozenDict",
    "Ok",
    "Result",
    "TraceError",
    "WGraph",
    "WNode",
    "WStep",
    "Maybe",
    "Nothing",
    "NOTHING",
    "Some",
    "trace_err",
]
