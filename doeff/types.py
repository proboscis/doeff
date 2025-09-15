"""
Core types for the doeff effects system.

This module contains the foundational types with zero internal dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, TypeVar, Union, TYPE_CHECKING

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

    def copy(self) -> ExecutionContext:
        """Create a shallow copy of the context."""
        return ExecutionContext(
            env=self.env.copy(),
            state=self.state.copy(),
            log=self.log.copy(),
            graph=self.graph,
            io_allowed=self.io_allowed,
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