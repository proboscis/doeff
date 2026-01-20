"""
Handlers for doeff-omni-converter effects.

This module provides the convert_handler that processes ConvertEffects
by using a rulebook from the Reader environment.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING, Any

from doeff import do
from doeff.effects import ask, tell
from doeff.program import Program
from doeff.types import Effect
from doeff_omni_converter.effects import ConvertEffect
from doeff_omni_converter.rule import AutoData, KleisliRuleBook
from doeff_omni_converter.solver import solve

if TYPE_CHECKING:
    from doeff_omni_converter.rule import KleisliEdge


# Environment key for the rulebook
RULEBOOK_KEY = "rulebook"


@do
def handle_convert(effect: ConvertEffect) -> Generator[Effect | Program, Any, AutoData]:
    """
    Handle a ConvertEffect by finding and executing the conversion path.

    This handler:
    1. Retrieves the rulebook from the Reader environment
    2. Uses A* search to find the optimal conversion path
    3. Executes each Kleisli converter in sequence
    4. Returns an AutoData with the converted value

    The handler expects the rulebook to be in the environment under
    the key "rulebook" (RULEBOOK_KEY).

    Args:
        effect: ConvertEffect containing the data and target format

    Returns:
        AutoData with the converted value and target format

    Raises:
        ValueError: If no rulebook is found or no conversion path exists
    """
    # Get rulebook from environment
    rulebook: KleisliRuleBook = yield ask(RULEBOOK_KEY)
    if rulebook is None:
        raise ValueError(
            f"No rulebook found in environment. Set env['{RULEBOOK_KEY}'] = KleisliRuleBook(...)"
        )

    source_format = effect.data.format
    target_format = effect.target_format

    # Log the conversion request
    yield tell(
        {
            "event": "convert_start",
            "source": str(source_format),
            "target": str(target_format),
        }
    )

    # Find optimal conversion path
    edges: list[KleisliEdge] = solve(rulebook, source_format, target_format)

    # Log the path
    yield tell(
        {
            "event": "convert_path",
            "steps": [edge.name for edge in edges],
            "total_cost": sum(edge.cost for edge in edges),
        }
    )

    # Execute conversions in sequence (Kleisli composition)
    current_value = effect.data.value
    for edge in edges:
        # Each converter is a Kleisli arrow: Data -> Program[Data]
        current_value = yield edge.converter(current_value)

        yield tell(
            {
                "event": "convert_step",
                "step": edge.name,
                "dst_format": str(edge.dst_format),
            }
        )

    yield tell(
        {
            "event": "convert_complete",
            "target": str(target_format),
        }
    )

    return AutoData(current_value, target_format)


def convert_handler_interceptor(effect: Effect) -> Effect | Program:
    """
    Effect interceptor for ConvertEffect handling.

    Use this with Program.intercept() to handle ConvertEffects:

        program.intercept(convert_handler_interceptor)

    Or set up a custom handler pipeline.
    """
    if isinstance(effect, ConvertEffect):
        return handle_convert(effect)
    return effect


__all__ = [
    "RULEBOOK_KEY",
    "convert_handler_interceptor",
    "handle_convert",
]
