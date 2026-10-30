"""Image editing domain effect."""


from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from doeff import EffectBase
from doeff_image._types import ImageResult


@dataclass(frozen=True, kw_only=True)
class ImageEdit(EffectBase[ImageResult]):
    """Edit or transform one or more images with text guidance."""

    prompt: str
    model: str
    images: list[Image.Image] = field(default_factory=list)
    mask: Image.Image | None = None
    strength: float = 0.8
    generation_config: dict[str, Any] | None = None


__all__ = ["ImageEdit"]
