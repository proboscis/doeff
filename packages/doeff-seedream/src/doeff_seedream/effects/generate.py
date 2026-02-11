"""Image-generation effects for Seedream."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PIL import Image

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class SeedreamGenerate(EffectBase):
    """Request image generation from Seedream."""

    prompt: str
    model: str
    images: list[Image.Image] | None = None
    generation_config_overrides: dict[str, Any] | None = None
    max_retries: int = 3


__all__ = ["SeedreamGenerate"]
