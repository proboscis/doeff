"""Image-generation effects for Seedream."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from warnings import warn

from PIL import Image

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class SeedreamGenerate(EffectBase):
    """Deprecated alias for Seedream image editing/generation."""

    prompt: str
    model: str
    images: list[Image.Image] | None = None
    generation_config: dict[str, Any] | None = None
    generation_config_overrides: dict[str, Any] | None = None
    max_retries: int = 3

    def __post_init__(self) -> None:
        warn(
            "SeedreamGenerate is deprecated; use doeff_image.effects.ImageEdit instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if self.generation_config is None and self.generation_config_overrides is not None:
            object.__setattr__(self, "generation_config", dict(self.generation_config_overrides))


__all__ = ["SeedreamGenerate"]
