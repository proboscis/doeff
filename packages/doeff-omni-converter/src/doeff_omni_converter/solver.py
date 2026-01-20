"""
Kleisli-based A* solver for doeff-omni-converter.

This module implements an A* search algorithm that finds the optimal
conversion path between formats using KleisliEdges from a rulebook.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from doeff_omni_converter.rule import KleisliEdge, KleisliRuleBook


@dataclass(order=True)
class _SearchNode:
    """Internal node for A* search priority queue."""

    cost: int
    format: Any = field(compare=False)
    path: list[KleisliEdge] = field(compare=False, default_factory=list)


def solve(
    rulebook: KleisliRuleBook,
    source_format: Any,
    target_format: Any,
    max_iterations: int = 1000,
) -> list[KleisliEdge]:
    """
    Find the optimal conversion path using A* search.

    This implements a standard A* algorithm:
    1. Start from source_format with cost 0
    2. Expand by querying rulebook for available edges
    3. Track visited formats to avoid cycles
    4. Return path when target is reached

    The search is format-based (not value-based), so it only considers
    the structure of conversion edges. The actual Kleisli converters
    are executed later when the path is applied.

    Args:
        rulebook: KleisliRuleBook providing conversion edges
        source_format: Starting format
        target_format: Desired target format
        max_iterations: Safety limit to prevent infinite loops

    Returns:
        List of KleisliEdges representing the optimal conversion path.
        Empty list if source equals target.

    Raises:
        ValueError: If no conversion path exists or max_iterations exceeded
    """
    if source_format == target_format:
        return []

    # Priority queue: (cost, format, path)
    start = _SearchNode(cost=0, format=source_format, path=[])
    frontier: list[_SearchNode] = [start]
    heapq.heapify(frontier)

    # Track visited formats with their best cost
    visited: dict[Any, int] = {}

    iterations = 0
    while frontier and iterations < max_iterations:
        iterations += 1

        current = heapq.heappop(frontier)

        # Skip if we've found a better path to this format
        if current.format in visited and visited[current.format] <= current.cost:
            continue
        visited[current.format] = current.cost

        # Check if we've reached the target
        if current.format == target_format:
            return current.path

        # Expand: get available edges from current format
        edges = rulebook.get_edges(current.format)
        for edge in edges:
            new_cost = current.cost + edge.cost
            new_format = edge.dst_format

            # Only consider if we haven't found a better path
            if new_format not in visited or visited[new_format] > new_cost:
                new_path = current.path + [edge]
                new_node = _SearchNode(cost=new_cost, format=new_format, path=new_path)
                heapq.heappush(frontier, new_node)

    if iterations >= max_iterations:
        raise ValueError(
            f"A* search exceeded max_iterations ({max_iterations}). "
            f"No path found from {source_format} to {target_format}"
        )

    raise ValueError(f"No conversion path found from {source_format} to {target_format}")


def solve_lazy(
    rulebook: KleisliRuleBook,
    source_format: Any,
    target_format: Any,
    max_iterations: int = 1000,
) -> list[KleisliEdge] | None:
    """
    Like solve(), but returns None instead of raising on failure.

    Useful when you want to check if a conversion is possible
    without handling exceptions.
    """
    try:
        return solve(rulebook, source_format, target_format, max_iterations)
    except ValueError:
        return None


def can_convert(
    rulebook: KleisliRuleBook,
    source_format: Any,
    target_format: Any,
) -> bool:
    """Check if a conversion path exists between formats."""
    return solve_lazy(rulebook, source_format, target_format) is not None


def estimate_cost(
    rulebook: KleisliRuleBook,
    source_format: Any,
    target_format: Any,
) -> int | None:
    """
    Estimate the total cost of converting between formats.

    Returns None if no path exists.
    """
    path = solve_lazy(rulebook, source_format, target_format)
    if path is None:
        return None
    return sum(edge.cost for edge in path)


__all__ = [
    "can_convert",
    "estimate_cost",
    "solve",
    "solve_lazy",
]
