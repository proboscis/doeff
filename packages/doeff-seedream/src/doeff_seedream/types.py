"""Typed results exposed by the Seedream integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SeedreamImage:
    """Container for an image produced by Seedream."""

    image_bytes: bytes | None
    mime_type: str = "image/jpeg"
    url: str | None = None
    size: str | None = None

    def to_pil_image(self):  # type: ignore[override]
        """Decode the image into a :class:`PIL.Image.Image`."""
        if self.image_bytes is None:
            raise ValueError(
                "Seedream response did not include inline image bytes. "
                "Set generation_config_overrides['response_format'] = 'b64_json' to enable inline decoding."
            )
        from io import BytesIO

        from PIL import Image

        with BytesIO(self.image_bytes) as buffer:
            image = Image.open(buffer)
            return image.copy()

    def save(self, path: str, *, format: str | None = None) -> None:
        """Persist the image to disk."""

        image = self.to_pil_image()
        image.save(path, format=format)


@dataclass(frozen=True)
class SeedreamImageEditResult:
    """Result returned by :func:`doeff_seedream.structured_llm.edit_image__seedream4`."""

    images: list[SeedreamImage]
    prompt: str
    model: str
    raw_response: dict[str, Any]

    def to_pil_image(self):  # type: ignore[override]
        """Return the first generated image as a :class:`PIL.Image.Image`."""

        if not self.images:
            raise ValueError("Seedream response did not include any images")
        return self.images[0].to_pil_image()

    @property
    def image_bytes(self) -> bytes | None:
        """Convenience access to the primary image bytes."""

        if not self.images:
            return None
        return self.images[0].image_bytes

    def save(self, path: str, *, format: str | None = None) -> None:
        """Persist the primary image to disk."""

        image = self.to_pil_image()
        image.save(path, format=format)


__all__ = ["SeedreamImage", "SeedreamImageEditResult"]
