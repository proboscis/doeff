"""Image editing domain effect."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class ImageEdit(EffectBase):
    """Edit or transform one or more images with text guidance."""

    prompt: str
    model: str
    images: list[Image.Image] = field(default_factory=list)
    mask: Image.Image | None = None
    strength: float = 0.8
    generation_config: dict[str, Any] | None = None


__all__ = ["ImageEdit"]
