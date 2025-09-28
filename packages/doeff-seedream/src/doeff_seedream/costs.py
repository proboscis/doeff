"""Cost estimation helpers for Seedream image generation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

DEFAULT_COST_PER_IMAGE = 0.03
DEFAULT_SIZE_PRICING = {
    "1K": 0.015,
    "2K": 0.03,
    "4K": 0.06,
}
_RESOLUTION_PATTERN = re.compile(r"^(\d{2,5})x(\d{2,5})$")


@dataclass(frozen=True)
class CostEstimate:
    """Estimated cost for a Seedream request."""

    model: str
    size: str | None
    generated_images: int
    per_image_cost: float
    total_cost: float
    source: str


def _coerce_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _bucket_from_resolution(size: str) -> str:
    match = _RESOLUTION_PATTERN.match(size)
    if not match:
        return size
    width, height = (int(match.group(1)), int(match.group(2)))
    max_dimension = max(width, height)
    if max_dimension <= 1600:
        return "1K"
    if max_dimension <= 2700:
        return "2K"
    return "4K"


def _normalize_size(size: str | None) -> str | None:
    if not size:
        return None
    normalized = size.strip().upper()
    if normalized.endswith("PX"):
        normalized = normalized[:-2]
    if "X" in normalized and not normalized.endswith("K"):
        normalized = _bucket_from_resolution(normalized)
    return normalized


def _normalize_pricing_map(raw: Mapping[str, object] | None) -> dict[str, float]:
    if raw is None:
        return {}
    normalized: dict[str, float] = {}
    for key, value in raw.items():
        cost = _coerce_float(value)
        if cost is None:
            continue
        normalized[key.strip().upper()] = cost
    return normalized


def calculate_cost(
    model: str,
    *,
    generated_images: int,
    size: str | None,
    cost_per_size: Mapping[str, object] | None = None,
    default_cost: object = None,
) -> CostEstimate:
    """Estimate USD cost for a Seedream call."""

    if generated_images <= 0:
        raise ValueError("generated_images must be positive for cost estimation")

    normalized_size = _normalize_size(size)
    pricing_map = _normalize_pricing_map(cost_per_size)
    unit_cost = pricing_map.get(normalized_size or "")
    source = "size-override" if unit_cost is not None else "default"

    if unit_cost is None and normalized_size:
        unit_cost = DEFAULT_SIZE_PRICING.get(normalized_size)
        source = f"size:{normalized_size}" if unit_cost is not None else source

    if unit_cost is None:
        unit_cost = _coerce_float(default_cost)
        source = "env-default" if unit_cost is not None else source

    if unit_cost is None:
        unit_cost = DEFAULT_COST_PER_IMAGE
        source = "fallback"

    total_cost = unit_cost * float(generated_images)
    return CostEstimate(
        model=model,
        size=normalized_size,
        generated_images=generated_images,
        per_image_cost=unit_cost,
        total_cost=total_cost,
        source=source,
    )


__all__ = ["CostEstimate", "calculate_cost", "DEFAULT_COST_PER_IMAGE", "DEFAULT_SIZE_PRICING"]
