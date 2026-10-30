"""Image generation domain effect."""


from dataclasses import dataclass
from typing import Any

from doeff import EffectBase
from doeff_image._types import ImageResult


@dataclass(frozen=True, kw_only=True)
class ImageGenerate(EffectBase[ImageResult]):
    """Text-to-image generation."""

    prompt: str
    model: str
    size: tuple[int, int] | None = None
    style: str | None = None
    negative_prompt: str | None = None
    num_images: int = 1
    generation_config: dict[str, Any] | None = None


__all__ = ["ImageGenerate"]
