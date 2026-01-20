"""
Kleisli-based rules for doeff-omni-converter.

This module provides the core abstractions for Kleisli-style conversion rules:
- KleisliEdge: A single conversion step with a Kleisli arrow converter
- KleisliRuleBook: A collection of rules that produce edges for a given format
- AutoData: Self-describing data with format information
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from doeff.program import Program
    from doeff_omni_converter.effects import ConvertEffect


class KleisliConverter(Protocol):
    """Protocol for Kleisli arrow converters: Data -> Program[Data]."""

    def __call__(self, data: Any) -> Program[Any]:
        """Convert data, returning a Program for effectful conversion."""
        ...


@dataclass(frozen=True)
class KleisliEdge:
    """
    A single conversion edge with a Kleisli arrow converter.

    Represents one step in a conversion path. The converter is a Kleisli arrow
    (A -> Program[B]) that can perform effectful operations like IO, logging,
    or configuration lookup.

    A* solver compatibility: Search phase uses formats/costs only,
    execution phase runs the Kleisli converters.

    Attributes:
        converter: Kleisli arrow that performs the conversion
        dst_format: Target format after this conversion
        cost: Cost of this conversion (used by A* solver)
        name: Human-readable name for debugging/logging
    """

    converter: Callable[[Any], Program[Any]]  # Kleisli arrow: Data -> Program[Data]
    dst_format: Any  # Format
    cost: int
    name: str

    def __hash__(self) -> int:
        return hash((self.dst_format, self.cost, self.name))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, KleisliEdge):
            return (
                self.dst_format == other.dst_format
                and self.cost == other.cost
                and self.name == other.name
            )
        return NotImplemented


class Rule(Protocol):
    """Protocol for rule functions: Format -> List[KleisliEdge]."""

    def __call__(self, fmt: Any) -> Sequence[KleisliEdge]:
        """Return available conversion edges from the given format."""
        ...


@dataclass
class KleisliRuleBook:
    """
    A collection of rules for building conversion graphs.

    The rulebook holds multiple rule functions. Given a source format,
    it queries all rules and aggregates the available conversion edges.

    This design allows rules to be modular and composable:
    - Domain-specific rules (e.g., image conversion rules)
    - Custom rules for specific applications
    - Effectful rules that need runtime context

    Example:
        >>> def my_rules(fmt: Format) -> List[KleisliEdge]:
        >>>     if fmt == F.path:
        >>>         return [KleisliEdge(load_image, F.numpy(), 1, "load_from_path")]
        >>>     return []
        >>>
        >>> rulebook = KleisliRuleBook([my_rules])
    """

    rules: list[Callable[[Any], Sequence[KleisliEdge]]]

    def get_edges(self, fmt: Any) -> list[KleisliEdge]:
        """Get all available edges from the given format."""
        edges: list[KleisliEdge] = []
        for rule in self.rules:
            edges.extend(rule(fmt))
        return edges

    def add_rule(self, rule: Callable[[Any], Sequence[KleisliEdge]]) -> KleisliRuleBook:
        """Return a new rulebook with the additional rule."""
        return KleisliRuleBook(self.rules + [rule])

    def merge(self, other: KleisliRuleBook) -> KleisliRuleBook:
        """Merge two rulebooks."""
        return KleisliRuleBook(self.rules + other.rules)


@dataclass(frozen=True)
class AutoData:
    """
    Self-describing data with format information.

    AutoData holds a value together with its format specification,
    enabling automatic conversion path discovery.

    The `.to()` method returns a ConvertEffect (not the result directly),
    allowing the conversion to be deferred and composed with other effects.

    Example:
        >>> img = AutoData(path, F.path)
        >>> tensor = yield img.to(F.torch())  # Effectful conversion
        >>> print(tensor.value)  # Converted tensor
    """

    value: Any
    format: Any  # Format

    def to(self, target: Any) -> ConvertEffect:
        """
        Request conversion to the target format.

        Returns a ConvertEffect that will be handled by the convert_handler.
        The handler uses the rulebook from the Reader environment to find
        the optimal conversion path.

        Args:
            target: Target format specification

        Returns:
            ConvertEffect that yields an AutoData with the converted value
        """
        from doeff_omni_converter.effects import convert

        return convert(self, target)

    def cast(self, new_format: Any) -> AutoData:
        """
        Reinterpret the format without actual conversion (pure operation).

        This is useful when you know the value is already in a compatible
        format and just need to update the format specification.

        Args:
            new_format: New format to assign

        Returns:
            New AutoData with same value but different format
        """
        return AutoData(self.value, new_format)

    def map_value(self, f: Callable[[Any], Any]) -> AutoData:
        """
        Apply a pure function to the value, preserving format.

        Useful for simple transformations that don't change the format.
        """
        return AutoData(f(self.value), self.format)


__all__ = [
    "AutoData",
    "KleisliConverter",
    "KleisliEdge",
    "KleisliRuleBook",
    "Rule",
]
