"""Unified image result types."""


from dataclasses import dataclass
from typing import Any

from PIL import Image


@dataclass(frozen=True, kw_only=True)
class ImageResult:
    """Provider-agnostic image generation/edit result."""

    images: list[Image.Image]
    model: str
    prompt: str
    generation_id: str | None = None
    cost_usd: float | None = None
    raw_response: Any = None

    def to_pil_image(self):  # type: ignore[override]
        """Return the first image in the result."""
        if not self.images:
            raise ValueError("ImageResult has no images")
        return self.images[0]

    def save(self, path: str, *, format: str | None = None) -> None:
        """Save the first image in the result."""
        self.to_pil_image().save(path, format=format)


__all__ = ["ImageResult"]
