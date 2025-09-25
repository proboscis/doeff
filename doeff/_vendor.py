"""
Vendored minimal types from sge_hub.monads.state_graph_future_result
These types are ported to avoid circular dependencies.
Original source: sge-hub/src/sge_hub/monads/state_graph_future_result/
"""

from __future__ import annotations

import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from frozendict import frozendict

# =========================================================
# Type Vars
# =========================================================
T = TypeVar("T")

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
class Ok(Generic[T]):
    """Success result."""
    value: T


@dataclass(frozen=True)
class Err:
    """Error result."""
    error: TraceError


Result = Ok[T] | Err

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
    "trace_err",
]
